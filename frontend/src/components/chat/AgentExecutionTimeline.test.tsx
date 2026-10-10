import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { StageTiming } from '@/lib/types'
import {
  AgentExecutionTimeline,
  aggregateStageTimings,
  formatStageName,
} from './AgentExecutionTimeline'

function timing(stage: string, attempt: number, durationMs: number): StageTiming {
  return { stage, attempt, duration_ms: durationMs }
}

/** The timings a question that retried once produces: `generate_sql` runs
 * twice, so the stage has two calls. */
const RETRIED_RUN: StageTiming[] = [
  timing('retrieve_schema', 1, 120.5),
  timing('generate_sql', 1, 3400),
  timing('validate_sql', 1, 8.2),
  timing('generate_sql', 2, 2900),
  timing('validate_sql', 2, 7.1),
  timing('execute_sql', 2, 45),
]

/** `Expander` is a native <details>/<summary>, so its content is already
 * in the DOM; this opens it the way a click would, for the tests that
 * care about the visible state. */
function open() {
  const summary = screen.getByText(/Agent execution timeline/)
  fireEvent.click(summary)
  return summary
}

/** The collapsed title text, which carries the stage count and total. */
function title() {
  return screen.getByText(/Agent execution timeline/).textContent ?? ''
}

describe('aggregateStageTimings', () => {
  it('sums a stage that ran more than once and counts the calls', () => {
    const rows = aggregateStageTimings(RETRIED_RUN)
    const generate = rows.find((row) => row.stage === 'generate_sql')
    expect(generate).toEqual({ stage: 'generate_sql', totalMs: 6300, calls: 2 })
  })

  it('keeps pipeline order rather than sorting by duration', () => {
    // The order the stages ran is itself information, and a list that
    // reorders between questions is harder to scan.
    expect(aggregateStageTimings(RETRIED_RUN).map((row) => row.stage)).toEqual([
      'retrieve_schema',
      'generate_sql',
      'validate_sql',
      'execute_sql',
    ])
  })

  it('returns nothing for no timings', () => {
    expect(aggregateStageTimings([])).toEqual([])
  })
})

describe('formatStageName', () => {
  it('turns a node name into a readable label', () => {
    expect(formatStageName('generate_sql')).toBe('Generate sql')
    expect(formatStageName('retrieve_business_context')).toBe('Retrieve business context')
  })

  it('leaves an unrecognized name usable rather than blank', () => {
    // Derived, not a hand-maintained map, so a newly added node still
    // gets a label instead of falling back to a raw identifier.
    expect(formatStageName('some_future_node')).toBe('Some future node')
    expect(formatStageName('')).toBe('')
  })
})

describe('AgentExecutionTimeline', () => {
  it('renders nothing when the run recorded no timings', () => {
    // A rejected question records none; the panel must not appear empty.
    const { container } = render(<AgentExecutionTimeline timings={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the stage count and total duration in the collapsed title', () => {
    render(<AgentExecutionTimeline timings={RETRIED_RUN} />)
    // 120.5 + 6300 + 15.3 + 45 = 6480.8ms
    expect(title()).toMatch(/Agent execution timeline \(4 stages, 6\.48s\)/)
  })

  it('uses the singular label for a single stage', () => {
    render(<AgentExecutionTimeline timings={[timing('execute_sql', 1, 50)]} />)
    expect(title()).toMatch(/\(1 stage, 50ms\)/)
  })

  it('lists one row per stage with its aggregated duration', () => {
    render(<AgentExecutionTimeline timings={RETRIED_RUN} />)
    open()
    const list = screen.getByRole('list', { name: 'Agent execution timeline' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(rows[1]).toHaveTextContent('Generate sql')
    expect(rows[1]).toHaveTextContent('6.30s')
  })

  it('discloses that a stage ran more than once', () => {
    render(<AgentExecutionTimeline timings={RETRIED_RUN} />)
    open()
    const list = screen.getByRole('list', { name: 'Agent execution timeline' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows[1]).toHaveTextContent('2 calls')
    // A stage that ran once says nothing -- "1 call" is noise.
    expect(rows[0]).not.toHaveTextContent('call')
  })

  it('formats sub-second durations in milliseconds', () => {
    render(<AgentExecutionTimeline timings={[timing('validate_sql', 1, 8.2)]} />)
    open()
    expect(screen.getByText('8ms')).toBeInTheDocument()
  })

  it('keeps the bar out of the accessibility tree', () => {
    // The bar is decorative: the duration beside it carries the same
    // information for a screen reader.
    const { container } = render(<AgentExecutionTimeline timings={RETRIED_RUN} />)
    open()
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0)
  })

  it('survives a zero-duration run without dividing by zero', () => {
    render(<AgentExecutionTimeline timings={[timing('execute_sql', 1, 0)]} />)
    open()
    expect(screen.getByText('0ms')).toBeInTheDocument()
  })

  it('exposes only node names and durations', () => {
    // No prompt text, no model output, no intermediate reasoning -- the
    // panel can only render what `stage_timings` carries.
    const { container } = render(<AgentExecutionTimeline timings={RETRIED_RUN} />)
    open()
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/SELECT|prompt|reasoning/i)
  })
})
