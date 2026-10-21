import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { KNOWN_NODES } from '@/components/sql/QueryLifecycle'
import type { AgentStatus, StageTiming } from '@/lib/types'
import {
  AgentExecutionTimeline,
  LABELLED_NODES,
  aggregateStageTimings,
  deriveVerdict,
  formatStageName,
  reasonKeyFor,
} from './AgentExecutionTimeline'

function ok(stage: string, attempt: number, durationMs: number): StageTiming {
  return { stage, attempt, duration_ms: durationMs, outcome: 'ok', error_category: null }
}

function noOp(stage: string, attempt: number, durationMs = 0.2): StageTiming {
  return { stage, attempt, duration_ms: durationMs, outcome: 'no_op', error_category: null }
}

function failed(
  stage: string,
  attempt: number,
  durationMs: number,
  category: string,
): StageTiming {
  return { stage, attempt, duration_ms: durationMs, outcome: 'error', error_category: category }
}

/** A plain successful run, shaped like the real one this feature was
 * verified against: the optional stages that did nothing report `no_op`. */
const NORMAL_RUN: StageTiming[] = [
  ok('sanitize_input', 1, 1.2),
  ok('classify_followup', 1, 0.2),
  ok('retrieve_schema', 1, 38),
  noOp('retrieve_golden_examples', 1),
  ok('retrieve_business_context', 1, 0.5),
  noOp('classify_analytical_intent', 1),
  noOp('build_analytical_plan', 1),
  noOp('plan_query', 1),
  ok('generate_sql', 1, 3400),
  noOp('review_sql', 1),
  noOp('review_metric_conformance', 1),
  noOp('enforce_display_names', 1),
  ok('validate_sql', 1, 5.3),
  noOp('estimate_cost', 1),
  ok('execute_sql', 1, 45),
]

/** The same run, but `execute_sql` hit a missing column on attempt 1 and
 * the graph routed back through `retrieve_schema`. Taken from a real
 * `run_agent()` trace, not invented. */
const RETRIED_RUN: StageTiming[] = [
  ok('sanitize_input', 1, 1.2),
  ok('retrieve_schema', 1, 38),
  ok('generate_sql', 1, 3400),
  ok('validate_sql', 1, 5.3),
  failed('execute_sql', 1, 12, 'missing_reference'),
  ok('retrieve_schema', 2, 20),
  ok('generate_sql', 2, 2900),
  ok('validate_sql', 2, 4.1),
  ok('execute_sql', 2, 45),
]

function renderTimeline(timings: StageTiming[], agentStatus: AgentStatus = 'succeeded') {
  return render(<AgentExecutionTimeline timings={timings} agentStatus={agentStatus} />)
}

/** `Expander` is a native <details>/<summary>, so the body is already in
 * the DOM; this opens it the way a click would. */
function open() {
  fireEvent.click(screen.getByText(/Agent/))
}

/** The whole collapsed summary line. The verdict, total and retry count
 * are separate spans inside it, so reading the matched element alone
 * would get only the verdict. */
function summaryText() {
  return screen.getByText(/Agent/).closest('summary')?.textContent ?? ''
}

function listRows() {
  const list = screen.getByRole('list', { name: 'Agent execution timeline' })
  return within(list).getAllByRole('listitem')
}

describe('aggregateStageTimings', () => {
  it('sums a stage that ran more than once and counts the calls', () => {
    const rows = aggregateStageTimings(RETRIED_RUN)
    const generate = rows.find((row) => row.stage === 'generate_sql')
    expect(generate).toMatchObject({ totalMs: 6300, calls: 2, status: 'ok' })
  })

  it('keeps pipeline order rather than sorting by duration', () => {
    // The order the stages ran is itself information, and a list that
    // reorders between questions is harder to scan.
    expect(aggregateStageTimings(RETRIED_RUN).map((row) => row.stage)).toEqual([
      'sanitize_input',
      'retrieve_schema',
      'generate_sql',
      'validate_sql',
      'execute_sql',
    ])
  })

  it('marks a stage that errored then ran again as retried, not failed', () => {
    // `execute_sql` errored on attempt 1 and succeeded on attempt 2. That
    // is history, not where the run stopped -- which is why this cannot
    // be a "worst outcome wins" reduction.
    const execute = aggregateStageTimings(RETRIED_RUN).find((row) => row.stage === 'execute_sql')
    expect(execute?.status).toBe('retried')
    expect(execute?.errorCategory).toBeNull()
  })

  it('marks a stage whose last call errored as failed, and keeps its category', () => {
    const rows = aggregateStageTimings([
      ok('generate_sql', 1, 10),
      failed('validate_sql', 1, 5, 'safety_violation'),
    ])
    expect(rows.find((row) => row.stage === 'validate_sql')).toMatchObject({
      status: 'failed',
      errorCategory: 'safety_violation',
    })
  })

  it('marks a stage whose every call did nothing as skipped', () => {
    const rows = aggregateStageTimings([noOp('plan_query', 1), noOp('plan_query', 2)])
    expect(rows[0]?.status).toBe('skipped')
  })

  it('does not call a stage skipped once any call produced something', () => {
    const rows = aggregateStageTimings([noOp('plan_query', 1), ok('plan_query', 2, 900)])
    expect(rows[0]).toMatchObject({ status: 'ok', calls: 2, totalMs: 900.2 })
  })

  it('reads a timing with no outcome as ok rather than as a failure', () => {
    // An older cached response carries neither field; it must not render
    // as a run full of failures.
    const rows = aggregateStageTimings([{ stage: 'generate_sql', attempt: 1, duration_ms: 10 }])
    expect(rows[0]).toMatchObject({ status: 'ok', errorCategory: null })
  })

  it('returns nothing for no timings', () => {
    expect(aggregateStageTimings([])).toEqual([])
  })
})

describe('formatStageName', () => {
  it('labels every node the graph actually registers', () => {
    // `KNOWN_NODES` is pinned exhaustively against the real graph by the
    // server's own `test_existing_nodes_are_all_still_present`, so a node
    // added there has to be labelled here rather than silently falling
    // back to a derived name forever.
    expect([...KNOWN_NODES].sort()).toEqual([...LABELLED_NODES].sort())
  })

  it('reads as an operational stage, not as an identifier', () => {
    expect(formatStageName('generate_sql')).toBe('SQL generation')
    expect(formatStageName('execute_sql')).toBe('Read-only execution')
    expect(formatStageName('generate_insight')).toBe('Grounded insight')
  })

  it('leaves an unrecognized name usable rather than raw or blank', () => {
    // The fallback is why a newly added node still gets a readable label.
    expect(formatStageName('some_future_node')).toBe('Some future node')
    expect(formatStageName('')).toBe('')
  })
})

describe('reasonKeyFor', () => {
  it('maps every category this pipeline can record to a user-safe sentence', () => {
    // The vocabulary is `ExecutionErrorCategory`, the validator's
    // retryable violation types, and the terminal status names.
    for (const category of [
      'safety_violation',
      'restricted_column',
      'high_cost',
      'database_busy',
      'rejected',
      'rate_limited',
      'needs_clarification',
      'syntax',
      'aggregate_nesting',
      'nested_aggregate',
      'parse_error',
      'missing_reference',
      'timeout',
      'plan_not_satisfied',
      'metric_definition_not_used',
    ]) {
      expect(reasonKeyFor(category)).toMatch(/^lifecycle\.reason\./)
      expect(reasonKeyFor(category)).not.toBe('lifecycle.reason.unknown')
    }
  })

  it('falls back to the generic sentence rather than showing the category', () => {
    expect(reasonKeyFor('some_future_category')).toBe('lifecycle.reason.unknown')
    expect(reasonKeyFor(null)).toBe('lifecycle.reason.unknown')
  })
})

describe('deriveVerdict', () => {
  it('is stopped when a stage owns the failure', () => {
    const rows = aggregateStageTimings([failed('validate_sql', 1, 5, 'safety_violation')])
    expect(deriveVerdict(rows, 'failed')).toBe('stopped')
  })

  it('is failed when the run ended badly but no stage reported it', () => {
    // The retry budget ran out, or a stage raised before recording an
    // outcome -- a raising node appends no timing at all.
    expect(deriveVerdict(aggregateStageTimings(NORMAL_RUN), 'failed')).toBe('failed')
  })

  it('is completed for a successful run', () => {
    expect(deriveVerdict(aggregateStageTimings(NORMAL_RUN), 'succeeded')).toBe('completed')
  })
})

describe('AgentExecutionTimeline: normal execution', () => {
  it('reports completion, the total and the stage count when collapsed', () => {
    renderTimeline(NORMAL_RUN)
    // 1.2 + 0.2 + 38 + 0.5 + 3400 + 5.3 + 45 = 3490.2ms, plus 7 no-op
    // calls at 0.2ms = 3491.6ms.
    expect(summaryText()).toContain('3.49s')
    expect(summaryText()).toContain('7 stages')
  })

  it('says nothing about retries when there were none', () => {
    renderTimeline(NORMAL_RUN)
    expect(summaryText()).not.toMatch(/retr/i)
  })

  it('lists only the stages that did something for this question', () => {
    renderTimeline(NORMAL_RUN)
    open()
    const rows = listRows()
    expect(rows).toHaveLength(7)
    expect(rows.map((row) => row.textContent)).not.toContain(
      expect.stringContaining('Query plan'),
    )
    expect(screen.queryByText('Query plan')).not.toBeInTheDocument()
    expect(screen.getByText('SQL generation')).toBeInTheDocument()
  })

  it('discloses how many stages it left out rather than hiding them silently', () => {
    renderTimeline(NORMAL_RUN)
    open()
    expect(screen.getByText(/8 stage\(s\) that did nothing/)).toBeInTheDocument()
  })

  it('shows each stage duration, in milliseconds below a second', () => {
    renderTimeline(NORMAL_RUN)
    open()
    expect(screen.getByText('3.40s')).toBeInTheDocument()
    expect(screen.getByText('38ms')).toBeInTheDocument()
  })

  it('carries each stage status as text, not by icon or colour alone', () => {
    renderTimeline(NORMAL_RUN)
    open()
    expect(within(listRows()[0]).getByText(': completed')).toBeInTheDocument()
  })

  it('keeps the proportion bar out of the accessibility tree', () => {
    const { container } = renderTimeline(NORMAL_RUN)
    open()
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0)
  })
})

describe('AgentExecutionTimeline: retries', () => {
  it('reports the retry count in the collapsed summary', () => {
    renderTimeline(RETRIED_RUN)
    expect(summaryText()).toContain('1 retry')
  })

  it('counts retries from the attempt numbers, not from a second field', () => {
    renderTimeline([...RETRIED_RUN, failed('execute_sql', 3, 9, 'timeout')], 'failed')
    expect(summaryText()).toContain('2 retries')
  })

  it('marks the retried stage as retried and shows its call count', () => {
    renderTimeline(RETRIED_RUN)
    open()
    const execute = listRows().find((row) => row.textContent?.includes('Read-only execution'))
    expect(execute).toHaveTextContent('2 calls')
    expect(within(execute as HTMLElement).getByText(': retried, then completed')).toBeInTheDocument()
  })

  it('still reads as completed when a retry recovered', () => {
    renderTimeline(RETRIED_RUN)
    expect(screen.getByText(/Agent completed/)).toBeInTheDocument()
    expect(screen.queryByText(/Agent stopped/)).not.toBeInTheDocument()
  })

  it('says nothing about a stage that ran once -- "1 call" is noise', () => {
    renderTimeline(RETRIED_RUN)
    open()
    const sanitize = listRows()[0]
    expect(sanitize).not.toHaveTextContent('call')
  })
})

describe('AgentExecutionTimeline: validation failure', () => {
  const BLOCKED: StageTiming[] = [
    ok('sanitize_input', 1, 1),
    ok('retrieve_schema', 1, 30),
    ok('generate_sql', 1, 2000),
    failed('validate_sql', 1, 6, 'safety_violation'),
  ]

  it('names the stage that stopped the run, in the collapsed summary', () => {
    renderTimeline(BLOCKED, 'failed')
    expect(screen.getByText(/Agent stopped at SQL validation/)).toBeInTheDocument()
  })

  it('explains the refusal in user-safe language, never the category name', () => {
    renderTimeline(BLOCKED, 'failed')
    open()
    expect(screen.getByText(/SQL safety validator refused this query/)).toBeInTheDocument()
    expect(screen.queryByText(/safety_violation/)).not.toBeInTheDocument()
  })

  it('does not list stages downstream of the refusal', () => {
    renderTimeline(BLOCKED, 'failed')
    open()
    expect(screen.queryByText('Read-only execution')).not.toBeInTheDocument()
  })

  it('reports a restricted-column refusal without naming the column', () => {
    renderTimeline(
      [ok('generate_sql', 1, 10), failed('validate_sql', 1, 5, 'restricted_column')],
      'failed',
    )
    open()
    expect(screen.getByText(/field your role may not read/)).toBeInTheDocument()
  })
})

describe('AgentExecutionTimeline: timeout', () => {
  const TIMED_OUT: StageTiming[] = [
    ok('generate_sql', 1, 2000),
    ok('validate_sql', 1, 5),
    failed('execute_sql', 1, 15000, 'timeout'),
  ]

  it('attributes the timeout to the executing stage', () => {
    renderTimeline(TIMED_OUT, 'failed')
    expect(screen.getByText(/Agent stopped at Read-only execution/)).toBeInTheDocument()
  })

  it('explains it as a time limit rather than as an error', () => {
    renderTimeline(TIMED_OUT, 'failed')
    open()
    expect(screen.getByText(/took longer than the allowed time/)).toBeInTheDocument()
  })

  it('still shows the long stage duration it spent before giving up', () => {
    renderTimeline(TIMED_OUT, 'failed')
    open()
    expect(screen.getByText('15.00s')).toBeInTheDocument()
  })
})

describe('AgentExecutionTimeline: multi-source and non-SQL answers', () => {
  it('renders nothing for an answer the SQL pipeline never produced', () => {
    // A document/policy/web answer goes through `agent/orchestrator`, not
    // the SQL graph, so no node timing is recorded at all. An empty panel
    // would be worse than no panel.
    const { container } = renderTimeline([], 'succeeded')
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the SQL stages when a multi-source answer included SQL', () => {
    // The SQL branch of a fan-out still records its own node timings.
    renderTimeline([ok('retrieve_schema', 1, 30), ok('generate_sql', 1, 1500)])
    expect(screen.getByText(/Agent completed/)).toBeInTheDocument()
    open()
    expect(listRows()).toHaveLength(2)
  })

  it('renders nothing when every stage did nothing', () => {
    // Nothing meaningful to show, and the total alone would not be worth
    // a panel.
    const { container } = renderTimeline([noOp('plan_query', 1), noOp('review_sql', 1)])
    expect(container).toBeEmptyDOMElement()
  })
})

describe('AgentExecutionTimeline: disabled optional sources', () => {
  it('omits an optional stage that was switched off', () => {
    // `plan_query`/`review_sql` with `ENABLE_QUERY_PLANNING=false` are
    // pass-throughs: they ran, and contributed nothing.
    renderTimeline(NORMAL_RUN)
    open()
    for (const label of [
      'Query plan',
      'Analytical plan',
      'Plan conformance review',
      'Metric conformance review',
      'Cost estimate',
      'Golden examples',
      'Intent classification',
    ]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument()
    }
  })

  it('counts a skipped stage duration in the total all the same', () => {
    // The run really did spend that time; omitting it would understate
    // the total.
    renderTimeline([ok('generate_sql', 1, 1000), noOp('plan_query', 1, 500)])
    expect(summaryText()).toContain('1.50s')
  })

  it('shows an optional stage that was enabled and did produce something', () => {
    renderTimeline([ok('generate_sql', 1, 1000), ok('plan_query', 1, 800)])
    open()
    expect(screen.getByText('Query plan')).toBeInTheDocument()
  })
})

describe('AgentExecutionTimeline: degraded and malformed data', () => {
  it('renders a timing list from a payload that predates the outcome field', () => {
    // There is no event stream to fail (see the module docstring); the
    // equivalent degradation is a response carrying timings without the
    // newer fields, e.g. replayed from the session question cache.
    renderTimeline([
      { stage: 'retrieve_schema', attempt: 1, duration_ms: 30 },
      { stage: 'generate_sql', attempt: 1, duration_ms: 1500 },
    ])
    expect(screen.getByText(/Agent completed/)).toBeInTheDocument()
    open()
    expect(listRows()).toHaveLength(2)
  })

  it('reports a failed run honestly when no stage recorded the failure', () => {
    // The retry budget ran out, or a stage raised before recording an
    // outcome. Claiming completion here would be wrong.
    renderTimeline(NORMAL_RUN, 'failed')
    expect(screen.getByText(/Agent could not finish/)).toBeInTheDocument()
  })

  it('reports a rejected question as stopped at the stage that refused it', () => {
    renderTimeline([failed('sanitize_input', 1, 1.2, 'rejected')], 'rejected')
    expect(screen.getByText(/Agent stopped at Input check/)).toBeInTheDocument()
    open()
    expect(screen.getByText(/refused before any query was generated/)).toBeInTheDocument()
  })

  it('survives a zero-duration run without dividing by zero', () => {
    renderTimeline([ok('execute_sql', 1, 0)])
    open()
    expect(screen.getByText('0ms')).toBeInTheDocument()
  })

  it('shows a generic sentence for a category it does not recognize', () => {
    renderTimeline([failed('execute_sql', 1, 5, 'some_future_category')], 'failed')
    open()
    expect(screen.getByText(/could not produce a working query/)).toBeInTheDocument()
    expect(screen.queryByText(/some_future_category/)).not.toBeInTheDocument()
  })
})

describe('AgentExecutionTimeline: exposes no internals', () => {
  it('renders no SQL, prompt, reasoning or raw error text', () => {
    const { container } = renderTimeline(
      [
        ok('generate_sql', 1, 3400),
        failed('execute_sql', 1, 12, 'missing_reference'),
        ok('execute_sql', 2, 45),
      ],
      'succeeded',
    )
    open()
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/SELECT|prompt|reasoning|pyodbc|Traceback/i)
  })

  it('renders no raw error category anywhere', () => {
    // The failure text is a fixed sentence chosen by category; the
    // category itself is an internal name.
    const { container } = renderTimeline(
      [failed('validate_sql', 1, 5, 'safety_violation')],
      'failed',
    )
    open()
    expect(container.textContent ?? '').not.toMatch(/safety_violation|missing_reference/)
  })
})
