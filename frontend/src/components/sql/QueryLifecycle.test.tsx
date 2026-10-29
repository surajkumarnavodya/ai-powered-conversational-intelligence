import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { newHistoryEntry, type QueryHistoryEntry } from '@/lib/history'
import type { AgentStatus, AskResponse, AttemptRecord, StageTiming } from '@/lib/types'
import { askState } from '@/test/analyticsFixtures'
import {
  KNOWN_NODES,
  QueryLifecycle,
  deriveLifecycle,
  isPostResultNode,
  type LifecycleStageId,
  type LifecycleStatus,
} from './QueryLifecycle'

/** The full node sequence a question runs through, in pipeline order --
 * the same names `agent/graph.py` registers. A scenario truncates this at
 * whichever node it got to, which is exactly what `stage_timings` looks
 * like for a run that stopped there. */
const NODES = [
  'sanitize_input',
  'classify_followup',
  'retrieve_schema',
  'retrieve_golden_examples',
  'retrieve_business_context',
  'classify_analytical_intent',
  'build_analytical_plan',
  'plan_query',
  'generate_sql',
  'review_sql',
  'review_metric_conformance',
  'enforce_display_names',
  'validate_sql',
  'estimate_cost',
  'execute_sql',
] as const

function timings(upTo: (typeof NODES)[number]): StageTiming[] {
  const end = NODES.indexOf(upTo) + 1
  return NODES.slice(0, end).map((stage, index) => ({
    stage,
    attempt: 1,
    duration_ms: 10 * (index + 1),
  }))
}

function attempt(overrides: Partial<AttemptRecord> & { outcome: string }): AttemptRecord {
  return { attempt: 1, sql: 'SELECT 1', error: null, will_retry: false, ...overrides }
}

function entry(
  state: Partial<AskResponse> = {},
  overrides: Partial<QueryHistoryEntry> = {},
): QueryHistoryEntry {
  const base = newHistoryEntry('How many employees?', askState({ sql: 'SELECT 1', ...state }), 1200)
  return { ...base, ...overrides }
}

/** A finished, successful run that has not been confirmed yet. */
function readyEntry(
  state: Partial<AskResponse> = {},
  overrides: Partial<QueryHistoryEntry> = {},
): QueryHistoryEntry {
  return entry(
    {
      stage_timings: timings('execute_sql'),
      attempt_history: [attempt({ outcome: 'succeeded' })],
      ...state,
    },
    overrides,
  )
}

function statuses(stages: ReturnType<typeof deriveLifecycle>): Record<string, LifecycleStatus> {
  return Object.fromEntries(stages.map((stage) => [stage.id, stage.status]))
}

function statusOf(
  stages: ReturnType<typeof deriveLifecycle>,
  id: LifecycleStageId,
): LifecycleStatus | undefined {
  return stages.find((stage) => stage.id === id)?.status
}

/** The graph's real node set, mirroring the exhaustive assertion in
 * `tests/test_sql_agent_integration.py::test_existing_nodes_are_all_still_present`.
 * That test's docstring points back here, so the two stay paired. */
const GRAPH_NODES = [
  'sanitize_input',
  'classify_followup',
  'retrieve_schema',
  'retrieve_golden_examples',
  'retrieve_business_context',
  'classify_analytical_intent',
  'build_analytical_plan',
  'plan_query',
  'generate_sql',
  'review_sql',
  'review_metric_conformance',
  'enforce_display_names',
  'validate_sql',
  'estimate_cost',
  'execute_sql',
  'compute_analytics',
  'generate_forecast',
  'generate_recommendations',
  'generate_insight',
] as const

describe('stage map covers the real graph', () => {
  it('classifies every node the graph registers, as a stage or as post-result', () => {
    // A node in neither list would read as a stage that never started,
    // forever and silently -- the one way this rail could quietly lie.
    expect([...KNOWN_NODES].sort()).toEqual([...GRAPH_NODES].sort())
  })

  it('maps no node the graph does not have', () => {
    // Catches a typo in STAGE_NODES, which would otherwise surface only
    // as a stage stuck on "not started".
    for (const node of KNOWN_NODES) {
      expect(GRAPH_NODES).toContain(node)
    }
  })

  it('shows every node up to and including execution, and no analysis node', () => {
    const shown = KNOWN_NODES.filter((node) => !isPostResultNode(node))
    expect(shown).toContain('execute_sql')
    expect(shown).not.toContain('generate_insight')
    expect(shown).not.toContain('compute_analytics')
    // The nodes this rail deliberately hides all run after a result
    // exists, which is why hiding them costs the reader nothing.
    expect(KNOWN_NODES.filter(isPostResultNode)).toHaveLength(4)
  })

  it('covers the nodes the pipeline-order fixture walks', () => {
    // NODES below drives nearly every scenario in this file; if it ever
    // drifts from the real graph the scenarios would stop being realistic.
    for (const node of NODES) {
      expect(GRAPH_NODES).toContain(node)
    }
  })
})

describe('deriveLifecycle -- evidence, not assumption', () => {
  it('withholds the rail entirely when no stage timings were recorded', () => {
    // A turn reconstructed from saved chat history carries no
    // stage_timings, so there is no evidence of what ran. Guessing a
    // plausible rail there would be inventing success states.
    expect(deriveLifecycle(entry({ stage_timings: [] }))).toEqual([])
  })

  it('marks only the stages that actually ran, and leaves the rest not started', () => {
    // Stopped at validation: estimate_cost and execute_sql never ran.
    const stages = deriveLifecycle(
      entry({
        stage_timings: timings('validate_sql'),
        attempt_history: [attempt({ outcome: 'safety_violation', error: 'not allowed' })],
      }),
    )
    expect(statuses(stages)).toEqual({
      understanding: 'done',
      schema: 'done',
      sql: 'done',
      validation: 'blocked',
      cost: 'pending',
      ready: 'pending',
      executed: 'pending',
    })
  })

  it('groups the post-schema comprehension nodes with SQL, keeping the rail in pipeline order', () => {
    // retrieve_schema runs before plan_query, so a run that reached
    // planning must already show Schema as done -- not a later stage.
    const stages = deriveLifecycle(
      entry({ stage_timings: timings('plan_query'), attempt_history: [] }),
    )
    expect(statusOf(stages, 'schema')).toBe('done')
    expect(statusOf(stages, 'sql')).toBe('done')
    expect(stages.map((stage) => stage.id)).toEqual([
      'understanding',
      'schema',
      'sql',
      'validation',
      'cost',
      'ready',
      'executed',
    ])
  })
})

describe('deriveLifecycle -- success and the confirmation gate', () => {
  it('ends at "requires confirmation" for a successful run nobody has confirmed', () => {
    const stages = deriveLifecycle(readyEntry())
    expect(statuses(stages)).toEqual({
      understanding: 'done',
      schema: 'done',
      sql: 'done',
      validation: 'done',
      cost: 'done',
      ready: 'awaiting',
      executed: 'pending',
    })
  })

  it('completes both Ready and Executed once a confirmed result exists', () => {
    const stages = deriveLifecycle(
      readyEntry({}, { confirmedColumns: ['c'], confirmedRows: [[1], [2]] }),
    )
    expect(statusOf(stages, 'ready')).toBe('done')
    expect(statusOf(stages, 'executed')).toBe('done')
  })

  it('reports Executed as running while Confirm and Run is in flight', () => {
    // The only genuinely observable running state: the client itself
    // started this request. There is no streaming for the /ask phase.
    const stages = deriveLifecycle(readyEntry(), { isConfirming: true })
    expect(statusOf(stages, 'executed')).toBe('running')
    // Ready is no longer "awaiting" once the user has acted on it.
    expect(statusOf(stages, 'ready')).toBe('done')
  })

  it('never claims Executed from the agent own internal verification run', () => {
    // execute_sql ran (the agent executes candidates internally to
    // self-correct), but the user has seen nothing, so Executed must not
    // read as done. This is the distinction the whole stage split exists
    // for -- see the component docstring.
    const stages = deriveLifecycle(readyEntry())
    expect(statusOf(stages, 'executed')).not.toBe('done')
  })
})

describe('deriveLifecycle -- failure, blocking and the difference between them', () => {
  const cases: Array<{
    name: string
    outcome: string
    stage: LifecycleStageId
    status: LifecycleStatus
    ranTo: (typeof NODES)[number]
  }> = [
    {
      name: 'the SQL safety validator refusing',
      outcome: 'safety_violation',
      stage: 'validation',
      status: 'blocked',
      ranTo: 'validate_sql',
    },
    {
      name: 'a restricted column',
      outcome: 'restricted_column',
      stage: 'validation',
      status: 'blocked',
      ranTo: 'validate_sql',
    },
    {
      name: 'the cost ceiling',
      outcome: 'high_cost',
      stage: 'cost',
      status: 'blocked',
      ranTo: 'estimate_cost',
    },
    {
      name: 'the per-database concurrency limiter',
      outcome: 'database_busy',
      stage: 'ready',
      status: 'blocked',
      ranTo: 'execute_sql',
    },
    {
      name: 'a query timeout',
      outcome: 'timeout',
      stage: 'ready',
      status: 'failed',
      ranTo: 'execute_sql',
    },
    {
      name: 'a missing table or column',
      outcome: 'missing_reference',
      stage: 'ready',
      status: 'failed',
      ranTo: 'execute_sql',
    },
    {
      name: 'schema retrieval breaking',
      outcome: 'schema_retrieval_error',
      stage: 'schema',
      status: 'failed',
      ranTo: 'retrieve_schema',
    },
    {
      name: 'an unreachable model',
      outcome: 'llm_error',
      stage: 'sql',
      status: 'failed',
      ranTo: 'generate_sql',
    },
    {
      name: 'the generated SQL not matching the plan',
      outcome: 'plan_not_satisfied',
      stage: 'sql',
      status: 'failed',
      ranTo: 'review_sql',
    },
    {
      name: 'the governed metric definition being ignored',
      outcome: 'metric_definition_not_used',
      stage: 'sql',
      status: 'failed',
      ranTo: 'review_metric_conformance',
    },
    {
      // The agent stopping itself: an attempt handed back the previous
      // attempt's query unchanged, so the run ended rather than
      // spending the rest of the retry budget regenerating it
      // (UAT-readiness Phase 6, LOP-002). A generation-stage stop, not
      // a validation or execution one -- and `failed` rather than
      // `blocked`, because no gate refused anything.
      name: 'the model repeating itself instead of correcting',
      outcome: 'no_progress',
      stage: 'sql',
      status: 'failed',
      ranTo: 'generate_sql',
    },
  ]

  for (const scenario of cases) {
    it(`attributes ${scenario.name} to ${scenario.stage} as ${scenario.status}`, () => {
      const stages = deriveLifecycle(
        entry({
          stage_timings: timings(scenario.ranTo),
          attempt_history: [attempt({ outcome: scenario.outcome, error: 'x' })],
        }),
      )
      const stopped = stages.find((stage) => stage.id === scenario.stage)
      expect(stopped?.status).toBe(scenario.status)
      expect(stopped?.reasonKey).toBeTruthy()
    })
  }

  it('distinguishes a gate refusing from a stage failing, rather than calling both an error', () => {
    const blocked = deriveLifecycle(
      entry({
        stage_timings: timings('validate_sql'),
        attempt_history: [attempt({ outcome: 'safety_violation' })],
      }),
    )
    const failed = deriveLifecycle(
      entry({
        stage_timings: timings('execute_sql'),
        attempt_history: [attempt({ outcome: 'timeout' })],
      }),
    )
    expect(statusOf(blocked, 'validation')).toBe('blocked')
    expect(statusOf(failed, 'ready')).toBe('failed')
  })

  it('attributes an unrecognised outcome to the last stage that ran, not to a guess', () => {
    // `validate_sql_node` records its retryable violation categories by
    // their own names; a run that ended there never reached estimate_cost,
    // so the last stage that ran is the correct attribution.
    const stages = deriveLifecycle(
      entry({
        stage_timings: timings('validate_sql'),
        attempt_history: [attempt({ outcome: 'nested_aggregate', error: 'nested' })],
      }),
    )
    expect(statusOf(stages, 'validation')).toBe('failed')
    expect(statusOf(stages, 'cost')).toBe('pending')
  })

  it('does not read an advisory plan note as a failure', () => {
    // plan_not_satisfied_advisory is recorded with will_retry: false
    // because nothing is retried for it -- it is a note, not a refusal.
    const stages = deriveLifecycle(
      readyEntry({
        attempt_history: [
          attempt({ outcome: 'plan_not_satisfied_advisory' }),
          attempt({ attempt: 2, outcome: 'succeeded' }),
        ],
      }),
    )
    expect(stages.every((stage) => stage.status !== 'failed')).toBe(true)
    expect(statusOf(stages, 'ready')).toBe('awaiting')
  })

  it('falls back to the last stage that ran when a failed run recorded no attempt', () => {
    const stages = deriveLifecycle(
      entry({ stage_timings: timings('generate_sql'), attempt_history: [] }, {}),
      {},
    )
    // Nothing to attribute: status alone says it failed.
    const failedEntry = entry(
      { stage_timings: timings('generate_sql'), attempt_history: [] },
      { agentStatus: 'failed' as AgentStatus },
    )
    expect(statusOf(stages, 'sql')).toBe('done')
    expect(statusOf(deriveLifecycle(failedEntry), 'sql')).toBe('failed')
  })

  it('reports a confirmed run that failed as Executed/failed', () => {
    const stages = deriveLifecycle(
      readyEntry({}, { confirmedError: 'Invalid object name' }),
    )
    const executed = stages.find((stage) => stage.id === 'executed')
    expect(executed?.status).toBe('failed')
    expect(executed?.reasonKey).toBe('lifecycle.reason.executionFailed')
    // The server-side run itself did finish; only the user's run failed.
    expect(statusOf(stages, 'validation')).toBe('done')
  })
})

describe('deriveLifecycle -- refusals that happen before any SQL exists', () => {
  it('blocks at Understood for a rejected question', () => {
    const stages = deriveLifecycle(
      entry(
        {
          stage_timings: timings('sanitize_input'),
          rejection_reason: 'injection_detected',
          attempt_history: [],
        },
        { agentStatus: 'rejected' as AgentStatus },
      ),
    )
    const first = stages[0]
    expect(first.id).toBe('understanding')
    expect(first.status).toBe('blocked')
    expect(first.reasonKey).toBe('lifecycle.reason.refusedInput')
    expect(statusOf(stages, 'sql')).toBe('pending')
  })

  it('blocks at Understood for a rate-limited question', () => {
    const stages = deriveLifecycle(
      entry(
        { stage_timings: timings('sanitize_input'), attempt_history: [] },
        { agentStatus: 'rate_limited' as AgentStatus },
      ),
    )
    expect(stages[0].status).toBe('blocked')
    expect(stages[0].reasonKey).toBe('lifecycle.reason.rateLimited')
  })

  it('blocks at Understood when the question needs clarifying', () => {
    const stages = deriveLifecycle(
      entry(
        { stage_timings: timings('classify_followup'), attempt_history: [] },
        { agentStatus: 'needs_clarification' as AgentStatus },
      ),
    )
    expect(stages[0].status).toBe('blocked')
    expect(stages[0].reasonKey).toBe('lifecycle.reason.clarification')
  })

  it('maps each rejection reason to its own explanation', () => {
    const reasonFor = (rejection: string) =>
      deriveLifecycle(
        entry(
          {
            stage_timings: timings('sanitize_input'),
            rejection_reason: rejection,
            attempt_history: [],
          },
          { agentStatus: 'rejected' as AgentStatus },
        ),
      )[0].reasonKey

    expect(reasonFor('too_long')).toBe('lifecycle.reason.invalidInput')
    expect(reasonFor('off_topic')).toBe('lifecycle.reason.offTopic')
    // An unrecognised reason still gets a real explanation, never a blank.
    expect(reasonFor('something_new')).toBe('lifecycle.reason.refusedInput')
  })
})

describe('deriveLifecycle -- retries', () => {
  it('counts retried attempts against the stage that produced them', () => {
    const stages = deriveLifecycle(
      readyEntry({
        attempt_history: [
          attempt({ outcome: 'syntax_error', will_retry: true }),
          attempt({ attempt: 2, outcome: 'missing_reference', will_retry: true }),
          attempt({ attempt: 3, outcome: 'succeeded' }),
        ],
      }),
    )
    expect(stages.find((stage) => stage.id === 'ready')?.retries).toBe(2)
    expect(stages.find((stage) => stage.id === 'validation')?.retries).toBe(0)
  })

  it('does not count the terminal attempt as a retry', () => {
    const stages = deriveLifecycle(
      entry({
        stage_timings: timings('validate_sql'),
        attempt_history: [attempt({ outcome: 'safety_violation', will_retry: false })],
      }),
    )
    expect(stages.find((stage) => stage.id === 'validation')?.retries).toBe(0)
  })
})

describe('QueryLifecycle rendering', () => {
  it('renders nothing when there is no evidence of any stage', () => {
    const { container } = render(<QueryLifecycle entry={entry({ stage_timings: [] })} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the compact rail as an ordered list of stages', () => {
    render(<QueryLifecycle entry={readyEntry()} />)
    const rail = screen.getByRole('list', { name: 'Query lifecycle' })
    const items = within(rail).getAllByRole('listitem')
    expect(items).toHaveLength(7)
    expect(items[0]).toHaveTextContent('Understanding')
    expect(items[3]).toHaveTextContent('Validation')
    expect(items[6]).toHaveTextContent('Execution')
  })

  it('prompts for confirmation when that is what the query is waiting on', () => {
    render(<QueryLifecycle entry={readyEntry()} />)
    expect(screen.getByText(/Review the SQL below, then choose Confirm and Run/)).toBeInTheDocument()
  })

  it('shows a concise reason when a gate blocked the query', () => {
    render(
      <QueryLifecycle
        entry={entry({
          stage_timings: timings('validate_sql'),
          attempt_history: [attempt({ outcome: 'safety_violation' })],
        })}
      />,
    )
    expect(screen.getByText(/The SQL safety validator refused this query/)).toBeInTheDocument()
    // The confirmation prompt must not also appear -- nothing is waiting
    // on the user when the query was refused.
    expect(screen.queryByText(/choose Confirm and Run/)).not.toBeInTheDocument()
  })

  it('carries each stage status as text, not by colour or icon alone', () => {
    render(<QueryLifecycle entry={readyEntry()} />)
    const rail = screen.getByRole('list', { name: 'Query lifecycle' })
    // A stage label must read correctly under every status it can carry:
     // "Validation: blocked" is coherent where "Validated: blocked" would
     // have the label contradicting its own status.
    expect(rail).toHaveTextContent('Validation: completed')
    expect(rail).toHaveTextContent('Ready to run: awaiting your confirmation')
    expect(rail).toHaveTextContent('Execution: not started')
  })

  it('marks the stage the reader is waiting on as the current step', () => {
    const { container } = render(<QueryLifecycle entry={readyEntry()} />)
    const current = container.querySelectorAll('[aria-current="step"]')
    expect(current).toHaveLength(1)
    expect(current[0]).toHaveTextContent('Ready to run')
  })

  it('keeps the stage icons out of the accessibility tree', () => {
    const { container } = render(<QueryLifecycle entry={readyEntry()} />)
    expect(container.querySelectorAll('svg[aria-hidden="true"]').length).toBe(7)
  })

  it('shows the retry count on the stage that retried', () => {
    render(
      <QueryLifecycle
        entry={readyEntry({
          attempt_history: [
            attempt({ outcome: 'syntax_error', will_retry: true }),
            attempt({ attempt: 2, outcome: 'succeeded' }),
          ],
        })}
      />,
    )
    expect(screen.getByText('1 retries')).toBeInTheDocument()
  })

  it('labels a blocked stage without claiming it succeeded', () => {
    render(
      <QueryLifecycle
        entry={entry({
          stage_timings: timings('validate_sql'),
          attempt_history: [attempt({ outcome: 'safety_violation' })],
        })}
      />,
    )
    const rail = screen.getByRole('list', { name: 'Query lifecycle' })
    expect(rail).toHaveTextContent('Validation: blocked')
    // The success word must not appear anywhere on a rail whose query was
    // refused -- not as a stage label, not as a status.
    expect(rail.textContent ?? '').not.toMatch(/Validated/)
  })

  it('never shows a raw error, a credential or a validator rule name', () => {
    // The reason strings are fixed and chosen by outcome class: the
    // server's own message, and the specific safety rule that fired, both
    // stay out of this always-visible rail.
    const { container } = render(
      <QueryLifecycle
        entry={entry({
          stage_timings: timings('validate_sql'),
          attempt_history: [
            attempt({
              outcome: 'safety_violation',
              error: 'system_catalog_access: reference to sys.objects rejected',
            }),
          ],
        })}
      />,
    )
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/system_catalog_access|sys\.objects/)
    expect(text).not.toMatch(/password|Server=|connection string/i)
    expect(text).not.toMatch(/system prompt|chain of thought|reasoning:/i)
  })
})

describe('streaming', () => {
  it('renders from final state only, with no partial-progress input', () => {
    // POST /ask is a single request with no SSE/WebSocket counterpart
    // anywhere in api/, so there is no mid-run stage signal to consume.
    // deriveLifecycle therefore takes no stream/progress argument at all:
    // its only inputs are the completed response and whether the client's
    // own Confirm and Run is in flight. If streaming is ever added, this
    // test is the one that should change.
    // One required argument -- the completed response. Nothing mid-run is
    // passed in, because nothing mid-run is observable.
    expect(deriveLifecycle.length).toBe(1)
    const stages = deriveLifecycle(readyEntry())
    // So no stage can ever read as running from server state alone.
    expect(stages.every((stage) => stage.status !== 'running')).toBe(true)
    // The one running state is the client's own in-flight confirmation.
    expect(
      deriveLifecycle(readyEntry(), { isConfirming: true }).some(
        (stage) => stage.status === 'running',
      ),
    ).toBe(true)
  })
})
