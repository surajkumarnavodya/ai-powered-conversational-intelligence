import { Check, CircleDashed, CircleDot, Loader2, RotateCcw, ShieldAlert, XCircle } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { QueryHistoryEntry } from '@/lib/history'
import { cn } from '@/lib/utils'

/** How far this question got through the safe execution lifecycle, and
 * where it stopped.
 *
 * `✓ Understanding  ✓ Schema  ✓ SQL  ✓ Validation  ✓ Cost check  ● Ready to run  ○ Execution`
 *
 * Each stage is labelled with a noun rather than a past participle,
 * because one label is rendered under all six statuses: "Validation:
 * blocked" is coherent, where "Validated: blocked" would have the
 * stage name contradicting its own status.
 *
 * **What this adds that nothing else showed.** The turn already had three
 * run-related surfaces and none of them answered "where did this stop":
 * `RetryTimeline` lists each attempt's outcome, `AgentExecutionTimeline`
 * lists each stage's duration, and `ExecutionStatusBar` flags whether the
 * *result* can be trusted. A reader wanting "it got as far as validation
 * and was refused there" had to infer it from an attempt outcome code.
 *
 * **Every stage is evidence-backed; none is assumed.** A stage is shown as
 * reached only because `AskResponse.stage_timings` contains a real call to
 * one of its nodes -- `agent.nodes._timed_node` appends one entry per node
 * call for every node in the graph, so the set of stages present is
 * exactly how far the pipeline actually got. Nothing here infers a success
 * from the absence of an error.
 *
 * **Stage grouping follows real execution order, not a tidier story.**
 * `retrieve_schema` runs *before* the golden-example/business-context/
 * intent/analytical-plan/plan nodes, so "Understanding" covers only the
 * two pre-schema gates (`sanitize_input`, `classify_followup`) and the
 * comprehension nodes that run after schema retrieval are grouped with
 * `SQL`, which they immediately feed. Grouping them all under a single
 * leading "Understanding" chip would have put the rail out of order with
 * the pipeline it describes.
 *
 * **"Ready" is the agent's own verification; "Executed" is yours.** The
 * graph executes candidate SQL internally to detect and self-correct
 * runtime errors, but that result is never rendered (see CLAUDE.md, "SQL
 * is untrusted output, always"). So `ready` means "the agent has a query
 * it got to run, awaiting your confirmation" -- this codebase's real
 * requires-confirmation state -- and `executed` reflects only the user's
 * own Confirm and Run. Labelling the agent's internal run as "Executed"
 * would claim the user had seen a result they had not.
 *
 * **`blocked` and `failed` are different claims.** `blocked` means a
 * deterministic gate refused (the SQL safety validator, restricted-column
 * authorization, the cost ceiling, the input guard, a rate/concurrency
 * limit); `failed` means a stage tried and could not finish (schema
 * retrieval, an LLM error, a syntax error, a timeout). The split is read
 * from the outcome name the server already recorded, never re-derived --
 * `validate_sql_node` records the single fixed name `safety_violation`
 * for its fail-closed class and the specific category only for retryable
 * ones, so no copy of `SAFETY_VIOLATION_TYPES` is needed (or kept in
 * sync) on the client.
 *
 * **No streaming exists**, so this is final-state rendering. `POST /ask`
 * is a single request that returns once the graph has finished; there is
 * no SSE or WebSocket endpoint anywhere in `api/`, and therefore no
 * observable "currently on stage N" signal while a question is in flight.
 * `running` is used only where it is genuinely observable: the user's own
 * Confirm and Run, which the client itself initiated. The in-flight
 * `/ask` phase keeps `PendingTurn`'s existing "Thinking Ns…" badge rather
 * than a rail with a guessed active stage.
 *
 * **Reveals nothing new.** Stage identity comes from this app's own fixed
 * node names, and each reason is a fixed string chosen by outcome class --
 * never the server's raw error text, never the specific validator rule
 * that fired, never a model's reasoning.
 */

export type LifecycleStageId =
  | 'understanding'
  | 'schema'
  | 'sql'
  | 'validation'
  | 'cost'
  | 'ready'
  | 'executed'

/** `awaiting` is this codebase's "requires confirmation": validated,
 * cost-checked SQL that will not run until the user says so. */
export type LifecycleStatus = 'pending' | 'running' | 'done' | 'failed' | 'blocked' | 'awaiting'

export interface LifecycleStage {
  id: LifecycleStageId
  status: LifecycleStatus
  /** Retried attempts attributed to this stage, from `attempt_history`. */
  retries: number
  /** i18n key for a concise reason, set only for `failed`/`blocked`. */
  reasonKey: string | null
}

/** Display order, which is also the real pipeline order. */
const STAGE_ORDER: LifecycleStageId[] = [
  'understanding',
  'schema',
  'sql',
  'validation',
  'cost',
  'ready',
  'executed',
]

/** Which graph nodes make up each stage. `executed` has none: it is the
 * user's own Confirm and Run, not a node in the graph. */
const STAGE_NODES: Record<LifecycleStageId, readonly string[]> = {
  understanding: ['sanitize_input', 'classify_followup'],
  schema: ['retrieve_schema'],
  sql: [
    'retrieve_golden_examples',
    'retrieve_business_context',
    'classify_analytical_intent',
    'build_analytical_plan',
    'plan_query',
    'generate_sql',
    'review_sql',
    'review_metric_conformance',
  ],
  validation: ['validate_sql'],
  cost: ['estimate_cost'],
  ready: ['execute_sql'],
  executed: [],
}

/** Graph nodes deliberately absent from the rail above.
 *
 * These four run *after* a result exists and produce the analysis and
 * narrative around it (`AnalyticsSummary`, the insight block and the
 * recommendation chips already render their output). This rail is about
 * validation and execution status, so folding them in would add stages
 * that say nothing about whether the query was safe to run or did run.
 *
 * Listed rather than simply omitted so the omission reads as a decision.
 * Together with `STAGE_NODES` this must cover every node
 * `agent/graph.py` registers -- `tests/test_sql_agent_integration.py
 * ::test_existing_nodes_are_all_still_present` pins that node set
 * exhaustively on the server side and points here, so a node added there
 * has to be classified here too rather than silently reading as "not
 * started" forever.
 */
const POST_RESULT_NODES: readonly string[] = [
  'compute_analytics',
  'generate_forecast',
  'generate_recommendations',
  'generate_insight',
]

/** An attempt outcome mapped to the stage that produced it and whether a
 * gate refused (`blocked`) or the stage could not finish (`failed`).
 *
 * An outcome absent from this map is attributed to the last stage that
 * actually ran, as `failed`. That fallback is correct rather than lazy:
 * the unmapped names are `validate_sql_node`'s retryable violation
 * categories, and a run that ended there never reached `estimate_cost`,
 * so "the last stage that ran" is `validation` by construction.
 */
const OUTCOME_STAGES: Record<
  string,
  { stage: LifecycleStageId; status: 'failed' | 'blocked'; reasonKey: string }
> = {
  off_topic: { stage: 'understanding', status: 'blocked', reasonKey: 'lifecycle.reason.offTopic' },
  rate_limited: {
    stage: 'understanding',
    status: 'blocked',
    reasonKey: 'lifecycle.reason.rateLimited',
  },
  schema_retrieval_error: {
    stage: 'schema',
    status: 'failed',
    reasonKey: 'lifecycle.reason.schema',
  },
  llm_error: { stage: 'sql', status: 'failed', reasonKey: 'lifecycle.reason.llm' },
  plan_not_satisfied: { stage: 'sql', status: 'failed', reasonKey: 'lifecycle.reason.planMismatch' },
  metric_definition_not_used: {
    stage: 'sql',
    status: 'failed',
    reasonKey: 'lifecycle.reason.metric',
  },
  safety_violation: {
    stage: 'validation',
    status: 'blocked',
    reasonKey: 'lifecycle.reason.safety',
  },
  restricted_column: {
    stage: 'validation',
    status: 'blocked',
    reasonKey: 'lifecycle.reason.restricted',
  },
  high_cost: { stage: 'cost', status: 'blocked', reasonKey: 'lifecycle.reason.cost' },
  database_busy: { stage: 'ready', status: 'blocked', reasonKey: 'lifecycle.reason.busy' },
  timeout: { stage: 'ready', status: 'failed', reasonKey: 'lifecycle.reason.timeout' },
  missing_reference: {
    stage: 'ready',
    status: 'failed',
    reasonKey: 'lifecycle.reason.missingReference',
  },
  aggregate_nesting: { stage: 'ready', status: 'failed', reasonKey: 'lifecycle.reason.sqlError' },
  syntax_error: { stage: 'ready', status: 'failed', reasonKey: 'lifecycle.reason.sqlError' },
  unknown_error: { stage: 'ready', status: 'failed', reasonKey: 'lifecycle.reason.unknown' },
}

/** Outcomes that are not a stopping point at all.
 *
 * `plan_not_satisfied_advisory` is recorded with `will_retry: false`
 * because nothing is retried for it -- it is a note, not a refusal -- so
 * without this it would read exactly like a terminal failure.
 */
const NON_TERMINAL_OUTCOMES = new Set(['succeeded', 'plan_not_satisfied_advisory'])

/** Why the run stopped before generating anything, by rejection reason --
 * `agent.state.RejectionReason`'s own values. */
const REJECTION_REASONS: Record<string, string> = {
  injection_detected: 'lifecycle.reason.refusedInput',
  policy_refused: 'lifecycle.reason.offTopic',
  off_topic: 'lifecycle.reason.offTopic',
  too_long: 'lifecycle.reason.invalidInput',
  empty: 'lifecycle.reason.invalidInput',
}

function nodeToStage(node: string): LifecycleStageId | null {
  for (const id of STAGE_ORDER) {
    if (STAGE_NODES[id].includes(node)) return id
  }
  return null
}

/** True for a node this rail knows about but deliberately does not show.
 * Exported so a test can assert the two lists together account for every
 * node the graph registers. */
export function isPostResultNode(node: string): boolean {
  return POST_RESULT_NODES.includes(node)
}

/** Every node name this rail classifies, shown or not -- the union a test
 * compares against the real graph's own node set. */
export const KNOWN_NODES: readonly string[] = [
  ...STAGE_ORDER.flatMap((id) => [...STAGE_NODES[id]]),
  ...POST_RESULT_NODES,
]

/** The stage the run stopped at, if it stopped. */
function findStop(
  entry: QueryHistoryEntry,
  lastRan: LifecycleStageId | null,
): { stage: LifecycleStageId; status: 'failed' | 'blocked'; reasonKey: string } | null {
  const state = entry.finalState

  // A status-level refusal happened before, or instead of, any attempt --
  // the input guard and the rate limiter both stop the run outright.
  if (entry.agentStatus === 'rejected') {
    const mapped = state.rejection_reason ? REJECTION_REASONS[state.rejection_reason] : undefined
    return {
      stage: 'understanding',
      status: 'blocked',
      reasonKey: mapped ?? 'lifecycle.reason.refusedInput',
    }
  }
  if (entry.agentStatus === 'rate_limited') {
    return { stage: 'understanding', status: 'blocked', reasonKey: 'lifecycle.reason.rateLimited' }
  }
  if (entry.agentStatus === 'needs_clarification') {
    return {
      stage: 'understanding',
      status: 'blocked',
      reasonKey: 'lifecycle.reason.clarification',
    }
  }

  // Otherwise the stopping point is the last attempt that was not retried
  // and did not succeed.
  const terminal = [...state.attempt_history]
    .reverse()
    .find((attempt) => !attempt.will_retry && !NON_TERMINAL_OUTCOMES.has(attempt.outcome))
  if (terminal) {
    const mapped = OUTCOME_STAGES[terminal.outcome]
    if (mapped) return mapped
    return { stage: lastRan ?? 'sql', status: 'failed', reasonKey: 'lifecycle.reason.unknown' }
  }

  // `failed` with nothing in `attempt_history` to attribute it to -- the
  // retry budget ran out, or a stage raised before recording an attempt.
  if (entry.agentStatus === 'failed') {
    return { stage: lastRan ?? 'sql', status: 'failed', reasonKey: 'lifecycle.reason.unknown' }
  }
  return null
}

/** Builds the rail from fields the server already returned.
 *
 * Exported for its own test: this mapping is the whole logic of the
 * component, and asserting on it directly is clearer than reading it back
 * out of rendered chips.
 *
 * Returns `[]` when `stage_timings` is empty -- a turn reconstructed from
 * saved chat history carries no stage timings (they are not persisted), so
 * there is no evidence of what ran and the rail is withheld rather than
 * guessed.
 */
export function deriveLifecycle(
  entry: QueryHistoryEntry,
  { isConfirming = false }: { isConfirming?: boolean } = {},
): LifecycleStage[] {
  const state = entry.finalState
  const timings = state.stage_timings ?? []
  if (timings.length === 0) return []

  const ran = new Set<LifecycleStageId>()
  for (const timing of timings) {
    const stage = nodeToStage(timing.stage)
    if (stage) ran.add(stage)
  }
  const lastRan = [...STAGE_ORDER].reverse().find((id) => ran.has(id)) ?? null

  // Retries are attributed through the same outcome map, so a stage's
  // retry count and its stopping status can never disagree about which
  // stage an outcome belongs to.
  const retries = new Map<LifecycleStageId, number>()
  for (const attempt of state.attempt_history) {
    if (!attempt.will_retry) continue
    const stage = OUTCOME_STAGES[attempt.outcome]?.stage ?? lastRan
    if (!stage) continue
    retries.set(stage, (retries.get(stage) ?? 0) + 1)
  }

  const stop = findStop(entry, lastRan)
  const stopIndex = stop ? STAGE_ORDER.indexOf(stop.stage) : -1
  const hasConfirmedResult = entry.confirmedRows !== null || entry.confirmedError !== null

  return STAGE_ORDER.map((id, index): LifecycleStage => {
    const base = { id, retries: retries.get(id) ?? 0, reasonKey: null }

    if (stop && index === stopIndex) {
      return { ...base, status: stop.status, reasonKey: stop.reasonKey }
    }
    // Nothing downstream of the stopping point ever ran.
    if (stop && index > stopIndex) return { ...base, status: 'pending' }

    if (id === 'executed') {
      if (isConfirming) return { ...base, status: 'running' }
      if (entry.confirmedError !== null) {
        return { ...base, status: 'failed', reasonKey: 'lifecycle.reason.executionFailed' }
      }
      if (entry.confirmedRows !== null) return { ...base, status: 'done' }
      return { ...base, status: 'pending' }
    }

    if (!ran.has(id)) return { ...base, status: 'pending' }

    // Reached, and the pipeline did not stop here. `ready` is the one
    // stage whose completion depends on the user rather than the server.
    if (id === 'ready' && !hasConfirmedResult && !isConfirming) {
      return { ...base, status: 'awaiting' }
    }
    return { ...base, status: 'done' }
  })
}

const ICONS: Record<LifecycleStatus, typeof Check> = {
  done: Check,
  running: Loader2,
  failed: XCircle,
  blocked: ShieldAlert,
  awaiting: CircleDot,
  pending: CircleDashed,
}

const TONES: Record<LifecycleStatus, string> = {
  done: 'text-[var(--success)]',
  running: 'text-[var(--accent)]',
  failed: 'text-[var(--danger)]',
  blocked: 'text-[var(--warning)]',
  awaiting: 'text-[var(--accent)]',
  pending: 'text-[var(--muted-foreground)] opacity-60',
}

export function QueryLifecycle({
  entry,
  isConfirming = false,
}: {
  entry: QueryHistoryEntry
  /** True while this turn's own Confirm and Run is in flight -- the one
   * genuinely observable `running` state (see this module's docstring on
   * why the `/ask` phase has none). */
  isConfirming?: boolean
}) {
  const { t } = useTranslation()
  const stages = useMemo(() => deriveLifecycle(entry, { isConfirming }), [entry, isConfirming])

  if (stages.length === 0) return null

  const stopped = stages.find((stage) => stage.status === 'failed' || stage.status === 'blocked')
  const awaiting = stages.find((stage) => stage.status === 'awaiting')

  return (
    <div className="flex flex-col gap-1">
      <ol
        className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"
        aria-label={t('lifecycle.title')}
      >
        {stages.map((stage) => {
          const Icon = ICONS[stage.status]
          const isActive = stage.status === 'running' || stage.status === 'awaiting'
          return (
            <li
              key={stage.id}
              className={cn('inline-flex items-center gap-1', TONES[stage.status])}
              {...(isActive ? { 'aria-current': 'step' as const } : {})}
            >
              <Icon
                className={cn('h-3.5 w-3.5 shrink-0', stage.status === 'running' && 'animate-spin')}
                aria-hidden="true"
              />
              <span>{t(`lifecycle.stage.${stage.id}`)}</span>
              {/* The status is carried as text, not by colour or icon
                  alone, so it survives a screen reader and a monochrome
                  or colour-blind reading of the rail. */}
              <span className="sr-only">: {t(`lifecycle.status.${stage.status}`)}</span>
              {stage.retries > 0 && (
                <span
                  className="inline-flex items-center gap-0.5 text-[var(--muted-foreground)]"
                  title={t('lifecycle.retriesDetail')}
                >
                  <RotateCcw className="h-3 w-3 shrink-0" aria-hidden="true" />
                  {t('lifecycle.retries', { count: stage.retries })}
                </span>
              )}
            </li>
          )
        })}
      </ol>
      {stopped?.reasonKey && (
        <p
          className={cn(
            'text-xs',
            stopped.status === 'blocked' ? 'text-[var(--warning)]' : 'text-[var(--danger)]',
          )}
        >
          {t(stopped.reasonKey)}
        </p>
      )}
      {!stopped && awaiting && (
        <p className="text-xs text-[var(--muted-foreground)]">{t('lifecycle.confirmHint')}</p>
      )}
    </div>
  )
}
