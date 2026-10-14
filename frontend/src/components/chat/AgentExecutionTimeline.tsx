import { Check, RotateCcw, XCircle } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Expander } from '@/components/ui/expander'
import type { AgentStatus, StageTiming } from '@/lib/types'
import { cn } from '@/lib/utils'

/** What the agent actually did, stage by stage: which operational stages
 * ran, how long each took, which were retried, and which one stopped the
 * run.
 *
 * Collapsed:  `Agent completed • 2.75s in 7 stages • 1 retry`
 * Expanded:   `✓ Schema retrieval      120ms`
 *             `↻ SQL generation        6.30s   2 calls`
 *             `✗ SQL validation          8ms`
 *             `  The SQL safety validator refused this query.`
 *
 * **High-level operational status, never reasoning.** Every field is read
 * from `AskResponse.stage_timings`, which carries only this application's
 * own fixed node names, wall-clock durations, an outcome enum and an
 * error *category*. There is no prompt text, no model output, no
 * intermediate value and no raw driver message anywhere in that payload,
 * so this panel cannot render one. The failure text shown is a fixed,
 * user-safe sentence chosen by category -- never the category name
 * itself, and never the server's own error string.
 *
 * **Per-stage status is the server's own judgment, not re-derived here.**
 * `agent.nodes._stage_outcome` classifies each node call from the dict
 * that node returned, at the moment it returned. A client deriving this
 * instead would have to read the shared `last_error_category`, which
 * persists across a retry and would therefore blame whichever node ran
 * *after* a failure -- `generate_sql`'s second attempt would inherit
 * `validate_sql`'s first-attempt violation. See `agent.state.StageOutcome`.
 *
 * **Only stages that were applicable to this question are listed.** A
 * stage whose every call reported `no_op` contributed nothing -- an
 * optional feature switched off (`plan_query` with
 * `ENABLE_QUERY_PLANNING=false`), or an enabled one that genuinely found
 * nothing (`retrieve_golden_examples` with no match above the similarity
 * floor). A real run has many: on a plain lookup question, 10 of 17 node
 * calls are `no_op`, so listing them would bury the seven that did the
 * work. Their duration is still counted in the total, because the run
 * really did spend it, and the omission is stated under the list rather
 * than left to be noticed.
 *
 * **There is no active/streaming state, because there is no stream.**
 * `POST /ask` is a single request that returns once the graph has
 * finished; there is no SSE or WebSocket route anywhere in `api/`, so no
 * "currently on stage N" signal exists while a question is in flight.
 * This panel renders final state only, and the in-flight phase keeps
 * `PendingTurn`'s existing live "Thinking Ns…" counter -- which is also
 * why nothing here polls: the one response carries the whole timeline.
 * Inventing a plausible in-progress rail would be guessing.
 *
 * **Relationship to the other run surfaces.** `QueryLifecycle` is the
 * always-visible rail over the seven coarse *safety gates* (how far
 * through validate -> cost -> confirm -> run, and where it stopped); this
 * panel is the collapsed, per-node record of the run (what ran, how long,
 * how many times, which node owned the failure). It replaced
 * `RetryTimeline`, which listed the same retries but rendered each
 * attempt's raw server error text (`str(exc)`) and raw outcome codes --
 * exactly the internals this panel is required not to expose -- and the
 * bare attempt count `SqlIntelligencePanel` used to carry, now in this
 * panel's collapsed summary where it is visible without a click.
 */

/** A stage's status, aggregated across its calls. `skipped` is computed
 * but never rendered (see the module docstring); it is a named state
 * rather than a silent filter so a test can assert on it. */
export type StageStatus = 'ok' | 'retried' | 'failed' | 'skipped'

export interface StageRow {
  stage: string
  totalMs: number
  calls: number
  status: StageStatus
  /** The failing call's error category, for `failed` only. Mapped to a
   * user-safe sentence before display; never rendered as-is. */
  errorCategory: string | null
}

/** A node name as a readable operational label.
 *
 * The map covers every node `agent/graph.py` registers, because the
 * mechanical fallback below reads badly for most of them ("Generate sql",
 * "Estimate query cost"). The fallback is kept all the same, so a node
 * added to the graph still gets a usable label instead of a raw
 * identifier -- and `AgentExecutionTimeline.test.tsx` asserts this map
 * covers `QueryLifecycle`'s `KNOWN_NODES`, which the server's own
 * `test_existing_nodes_are_all_still_present` pins exhaustively, so a new
 * node is noticed here rather than silently falling back forever.
 *
 * Deliberately not translated, exactly as the mechanical labels this
 * replaced were not: these are stage names in a diagnostic panel, and
 * adding 18 strings to five locale files for it would be out of
 * proportion. The panel's own chrome is translated.
 */
const STAGE_LABELS: Record<string, string> = {
  sanitize_input: 'Input check',
  classify_followup: 'Follow-up check',
  retrieve_schema: 'Schema retrieval',
  retrieve_golden_examples: 'Golden examples',
  retrieve_business_context: 'Business context',
  classify_analytical_intent: 'Intent classification',
  build_analytical_plan: 'Analytical plan',
  plan_query: 'Query plan',
  generate_sql: 'SQL generation',
  review_sql: 'Plan conformance review',
  review_metric_conformance: 'Metric conformance review',
  validate_sql: 'SQL validation',
  estimate_cost: 'Cost estimate',
  execute_sql: 'Read-only execution',
  compute_analytics: 'Analytics',
  generate_forecast: 'Forecast',
  generate_recommendations: 'Recommendations',
  generate_insight: 'Grounded insight',
}

/** An error category mapped to an existing user-safe sentence.
 *
 * The sentences are `QueryLifecycle`'s own `lifecycle.reason.*` keys,
 * reused rather than rewritten -- there is one set of failure wording in
 * this app. The *keys* are this map's own, because `error_category`
 * (`agent.error_classification.ExecutionErrorCategory`, the validator's
 * retryable violation types, and the terminal `status` names) is a
 * different vocabulary from `AttemptRecord.outcome`, which is what
 * `QueryLifecycle` keys on -- `syntax` here is `syntax_error` there.
 * Mapping one onto the other would have been a silent mismatch.
 */
const REASON_KEYS: Record<string, string> = {
  // Deterministic gates refusing.
  safety_violation: 'lifecycle.reason.safety',
  restricted_column: 'lifecycle.reason.restricted',
  high_cost: 'lifecycle.reason.cost',
  database_busy: 'lifecycle.reason.busy',
  rejected: 'lifecycle.reason.refusedInput',
  rate_limited: 'lifecycle.reason.rateLimited',
  needs_clarification: 'lifecycle.reason.clarification',
  // The database rejecting a query it was handed.
  syntax: 'lifecycle.reason.sqlError',
  aggregate_nesting: 'lifecycle.reason.sqlError',
  nested_aggregate: 'lifecycle.reason.sqlError',
  parse_error: 'lifecycle.reason.sqlError',
  missing_reference: 'lifecycle.reason.missingReference',
  timeout: 'lifecycle.reason.timeout',
  // The agent's own review steps not being satisfied.
  plan_not_satisfied: 'lifecycle.reason.planMismatch',
  metric_definition_not_used: 'lifecycle.reason.metric',
}

/** A run status that means the run ended without an answer. Mirrors
 * `agent.nodes._TERMINAL_STATUSES`. */
const TERMINAL_STATUSES: readonly AgentStatus[] = [
  'failed',
  'rejected',
  'rate_limited',
  'needs_clarification',
]

export function formatStageName(stage: string): string {
  const mapped = STAGE_LABELS[stage]
  if (mapped) return mapped
  const spaced = stage.replace(/_/g, ' ').trim()
  if (!spaced) return stage
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** Every node name this panel has a written label for -- exported so a
 * test can compare it against the graph's real node set. */
export const LABELLED_NODES: readonly string[] = Object.keys(STAGE_LABELS)

/** The user-safe sentence key for an error category, or the generic
 * fallback. Never returns the category name itself. */
export function reasonKeyFor(category: string | null): string {
  if (!category) return 'lifecycle.reason.unknown'
  return REASON_KEYS[category] ?? 'lifecycle.reason.unknown'
}

/** Aggregates per-call timings into one row per stage, in pipeline order.
 *
 * A stage's status is read from the *order* of its own calls: an errored
 * call followed by another call of the same stage was retried and
 * recovered; an errored last call is where the run stopped. That ordering
 * is why this cannot be a "worst outcome wins" reduction.
 */
export function aggregateStageTimings(timings: StageTiming[]): StageRow[] {
  const byStage = new Map<string, StageRow>()
  for (const timing of timings) {
    const outcome = timing.outcome ?? 'ok'
    const existing = byStage.get(timing.stage)
    if (!existing) {
      byStage.set(timing.stage, {
        stage: timing.stage,
        totalMs: timing.duration_ms,
        calls: 1,
        status: outcome === 'error' ? 'failed' : outcome === 'no_op' ? 'skipped' : 'ok',
        errorCategory: outcome === 'error' ? (timing.error_category ?? null) : null,
      })
      continue
    }
    existing.totalMs += timing.duration_ms
    existing.calls += 1
    if (outcome === 'error') {
      existing.status = 'failed'
      existing.errorCategory = timing.error_category ?? null
      continue
    }
    // This call did not fail. If an earlier call of the same stage did,
    // the stage was retried and recovered, so that failure is history
    // rather than where the run stopped.
    existing.status =
      existing.status === 'failed' || existing.status === 'retried'
        ? 'retried'
        : outcome === 'ok'
          ? 'ok'
          : existing.status
    existing.errorCategory = null
  }
  return [...byStage.values()]
}

/** The run's own verdict. `stopped` names the stage that owned the
 * failure; `failed` is a run that ended badly with no stage reporting it
 * (the retry budget ran out, or a stage raised before recording an
 * outcome -- a raising node appends no timing at all). */
export type Verdict = 'completed' | 'stopped' | 'failed'

export function deriveVerdict(rows: StageRow[], agentStatus: AgentStatus): Verdict {
  if (rows.some((row) => row.status === 'failed')) return 'stopped'
  if (TERMINAL_STATUSES.includes(agentStatus)) return 'failed'
  return 'completed'
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

type ShownStatus = Exclude<StageStatus, 'skipped'>

const ICONS: Record<ShownStatus, typeof Check> = {
  ok: Check,
  retried: RotateCcw,
  failed: XCircle,
}

const TONES: Record<ShownStatus, string> = {
  ok: 'text-[var(--success)]',
  retried: 'text-[var(--warning)]',
  failed: 'text-[var(--danger)]',
}

export function AgentExecutionTimeline({
  timings,
  agentStatus,
}: {
  timings: StageTiming[]
  agentStatus: AgentStatus
}) {
  const { t } = useTranslation()

  const { rows, skippedCount, totalMs, slowestMs, retries, verdict } = useMemo(() => {
    const aggregated = aggregateStageTimings(timings)
    const shown = aggregated.filter((row) => row.status !== 'skipped')
    return {
      rows: shown,
      skippedCount: aggregated.length - shown.length,
      // Every call's duration, including the stages not listed: the run
      // really did spend that time, so leaving it out would understate
      // the total.
      totalMs: timings.reduce((sum, timing) => sum + timing.duration_ms, 0),
      // The bar is scaled against the slowest listed stage rather than
      // the total, so the shape stays readable when one dominates (local
      // model inference routinely is 90%+ of a run).
      slowestMs: shown.reduce((max, row) => Math.max(max, row.totalMs), 0),
      // Attempt numbers are already on the timings, so the retry count
      // needs no second read of `attempt_history`.
      retries: timings.reduce((max, timing) => Math.max(max, timing.attempt), 1) - 1,
      verdict: deriveVerdict(aggregated, agentStatus),
    }
  }, [timings, agentStatus])

  if (rows.length === 0) return null

  const stopped = rows.find((row) => row.status === 'failed')
  const verdictText =
    verdict === 'stopped'
      ? t('sql.timelineStopped', { stage: formatStageName(stopped?.stage ?? '') })
      : verdict === 'failed'
        ? t('sql.timelineFailed')
        : t('sql.timelineCompleted')

  const summary = (
    <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span className={verdict === 'completed' ? undefined : 'text-[var(--danger)]'}>
        {verdictText}
      </span>
      <span className="font-normal text-[var(--muted-foreground)]">
        {'• '}
        {t('sql.timelineTotal', {
          duration: formatDuration(totalMs),
          count: rows.length,
          stages: rows.length === 1 ? t('sql.stage') : t('sql.stages'),
        })}
      </span>
      {retries > 0 && (
        <span className="font-normal text-[var(--muted-foreground)]">
          {'• '}
          {retries === 1 ? t('sql.timelineRetry') : t('sql.timelineRetries', { count: retries })}
        </span>
      )}
    </span>
  )

  return (
    <Expander title={summary}>
      <ul className="flex flex-col gap-1.5 text-xs" aria-label={t('sql.executionTimeline')}>
        {rows.map((row) => {
          const status = row.status as ShownStatus
          const Icon = ICONS[status]
          const share = slowestMs > 0 ? (row.totalMs / slowestMs) * 100 : 0
          return (
            <li key={row.stage} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Icon className={cn('h-3.5 w-3.5 shrink-0', TONES[status])} aria-hidden="true" />
              <span className="w-44 shrink-0 truncate" title={row.stage}>
                {formatStageName(row.stage)}
              </span>
              {/* The status is carried as text, not by icon or colour
                  alone, so it survives a screen reader and a monochrome
                  or colour-blind reading of the list. */}
              <span className="sr-only">: {t(`sql.stageStatus.${status}`)}</span>
              {/* Decorative: the duration beside it carries the same
                  information for a screen reader. */}
              <span
                aria-hidden="true"
                className="h-1.5 min-w-px flex-1 overflow-hidden rounded-full bg-[var(--muted)]"
              >
                <span
                  className="block h-full rounded-full bg-[var(--accent)]"
                  style={{ width: `${Math.max(share, 1)}%` }}
                />
              </span>
              <span className="w-16 shrink-0 text-right font-mono tabular-nums">
                {formatDuration(row.totalMs)}
              </span>
              <span className="w-20 shrink-0 text-right text-[var(--muted-foreground)]">
                {row.calls > 1 ? t('sql.stageCalls', { count: row.calls }) : ''}
              </span>
              {status === 'failed' && (
                <span className="w-full pl-5 text-[var(--danger)]">
                  {t(reasonKeyFor(row.errorCategory))}
                </span>
              )}
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-xs text-[var(--muted-foreground)]">
        {t('sql.executionTimelineHint')}
        {skippedCount > 0 && ` ${t('sql.timelineSkipped', { count: skippedCount })}`}
      </p>
    </Expander>
  )
}
