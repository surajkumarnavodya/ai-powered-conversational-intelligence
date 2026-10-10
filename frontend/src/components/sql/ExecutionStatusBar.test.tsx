import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { newHistoryEntry, type QueryHistoryEntry } from '@/lib/history'
import { askState } from '@/test/analyticsFixtures'
import type { AskResponse } from '@/lib/types'
import { ExecutionStatusBar, deriveStatusItems } from './ExecutionStatusBar'

/** i18next is configured in the test setup, so `t` returns the real
 * English strings; this stand-in keeps `deriveStatusItems`' unit tests
 * independent of that and asserts on the key, which is what matters. */
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

describe('deriveStatusItems', () => {
  it('reports validated once the server returned SQL', () => {
    expect(labels(deriveStatusItems(entry(), tKey))).toContain('status.validated')
  })

  it('reports nothing at all when there is no SQL and no result', () => {
    // A pure document/web answer has no query status to show.
    const items = deriveStatusItems(entry({ sql: null }), tKey)
    expect(items).toEqual([])
  })

  it('reports blocked when an attempt failed without retrying', () => {
    const items = deriveStatusItems(
      entry({
        attempt_history: [
          { attempt: 1, sql: 'DROP TABLE t', outcome: 'safety_violation', error: 'no', will_retry: false },
        ],
      }),
      tKey,
    )
    expect(labels(items)).toContain('status.blocked')
    expect(labels(items)).not.toContain('status.validated')
  })

  it('does not report blocked for a failure that was retried and then succeeded', () => {
    const items = deriveStatusItems(
      entry({
        attempt_history: [
          { attempt: 1, sql: 'SELECT x', outcome: 'invalid_column', error: 'bad', will_retry: true },
          { attempt: 2, sql: 'SELECT 1', outcome: 'succeeded', error: null, will_retry: false },
        ],
      }),
      tKey,
    )
    expect(labels(items)).toContain('status.validated')
    expect(labels(items)).toContain('status.attempts:2')
  })

  it('stays quiet about attempts when there was only one', () => {
    const items = deriveStatusItems(
      entry({
        attempt_history: [
          { attempt: 1, sql: 'SELECT 1', outcome: 'succeeded', error: null, will_retry: false },
        ],
      }),
      tKey,
    )
    // "1 attempt" is noise.
    expect(labels(items).some((label) => label.startsWith('status.attempts'))).toBe(false)
  })

  it('surfaces a server cost flag', () => {
    const items = deriveStatusItems(entry({ cost_notice: 'This scan is large.' }), tKey)
    const cost = items.find((item) => item.key === 'cost')
    expect(cost?.label).toBe('status.costFlagged')
    // The server's own wording is carried as the detail, never re-judged
    // on the client.
    expect(cost?.detail).toBe('This scan is large.')
  })

  it('reports the row count, duration and cache state after execution', () => {
    const items = deriveStatusItems(
      entry({}, {
        confirmedColumns: ['c'],
        confirmedRows: [[1], [2], [3]],
        confirmedDurationMs: 2500,
        confirmedCacheStatus: 'hit',
      }),
      tKey,
    )
    expect(labels(items)).toContain('status.rows:3')
    expect(labels(items)).toContain('2.50s')
    expect(labels(items)).toContain('status.cacheHit')
  })

  it('distinguishes a fresh execution from a cached one', () => {
    const items = deriveStatusItems(
      entry({}, {
        confirmedColumns: ['c'],
        confirmedRows: [[1]],
        confirmedCacheStatus: 'miss',
      }),
      tKey,
    )
    expect(labels(items)).toContain('status.cacheMiss')
  })

  it('reports an execution failure instead of a row count', () => {
    const items = deriveStatusItems(
      entry({}, { confirmedError: 'Invalid object name' }),
      tKey,
    )
    expect(labels(items)).toContain('status.executionFailed')
    expect(labels(items).some((label) => label.startsWith('status.rows'))).toBe(false)
  })

  it('flags a truncated result', () => {
    const items = deriveStatusItems(
      entry({}, {
        confirmedColumns: ['c'],
        confirmedRows: [[1]],
        confirmedTruncated: true,
      }),
      tKey,
    )
    expect(labels(items)).toContain('status.truncated')
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

  it('formats a sub-second duration in milliseconds', () => {
    const items = deriveStatusItems(
      entry({}, { confirmedColumns: ['c'], confirmedRows: [[1]], confirmedDurationMs: 42 }),
      tKey,
    )
    expect(labels(items)).toContain('42ms')
  })
})

describe('ExecutionStatusBar', () => {
  it('renders nothing when there is no status to report', () => {
    const { container } = render(<ExecutionStatusBar entry={entry({ sql: null })} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders each status as a list item under a labelled list', () => {
    render(
      <ExecutionStatusBar
        entry={entry({}, { confirmedColumns: ['c'], confirmedRows: [[1], [2]] })}
      />,
    )
    const list = screen.getByRole('list', { name: 'Query status' })
    expect(within(list).getAllByRole('listitem').length).toBeGreaterThanOrEqual(2)
    expect(within(list).getByText('Validated')).toBeInTheDocument()
    expect(within(list).getByText('2 rows')).toBeInTheDocument()
  })

  it('keeps the icons out of the accessibility tree', () => {
    // The label beside each icon carries the meaning.
    const { container } = render(<ExecutionStatusBar entry={entry()} />)
    expect(container.querySelectorAll('svg[aria-hidden="true"]').length).toBeGreaterThan(0)
  })

  it('never shows a credential or connection detail', () => {
    // Only fields the server already returned reach this component, and
    // the server redacts those; this pins that nothing here re-introduces
    // raw detail.
    const { container } = render(
      <ExecutionStatusBar
        entry={entry({ cost_notice: 'Large scan' }, { confirmedError: 'Invalid object name' })}
      />,
    )
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/password|Server=|connection string/i)
  })
})
