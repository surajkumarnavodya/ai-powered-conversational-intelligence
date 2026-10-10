import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Expander } from '@/components/ui/expander'
import type { StageTiming } from '@/lib/types'

/** Where a question's time actually went, per pipeline stage.
 *
 * Complements -- deliberately does not replace -- `RetryTimeline`. That
 * component answers "what happened on each attempt" (outcome, the SQL it
 * tried, the error); this one answers "which stage was slow". They read
 * the same run but different fields (`attempt_history` vs
 * `stage_timings`), and merging them would make one long panel that
 * answers neither question well.
 *
 * `stage_timings` carries one entry per node *call*, so a retried
 * question has several entries for the same stage. Those are aggregated
 * here into one row per stage with a call count, because "generate_sql
 * took 6.3s across 2 calls" is the useful reading; the per-attempt
 * breakdown is what `RetryTimeline` already shows.
 *
 * Rows stay in pipeline order (first occurrence), not sorted by
 * duration: the order the stages ran is itself information, and a list
 * that reorders itself between questions is harder to scan. The bar
 * makes the slow stage obvious without needing the sort.
 *
 * **Exposes only facts the server already records** -- node names and
 * wall-clock durations. No model reasoning, no prompt text, no
 * intermediate output.
 */

/** One stage's aggregated timing. */
interface StageRow {
  stage: string
  totalMs: number
  calls: number
}

/** A node name as a readable label: `generate_sql` -> `Generate sql`.
 *
 * Deliberately not a hand-maintained map of all eighteen node names --
 * that would silently fall back to a raw identifier the first time a
 * node is added or renamed, which is exactly when a label matters.
 */
export function formatStageName(stage: string): string {
  const spaced = stage.replace(/_/g, ' ').trim()
  if (!spaced) return stage
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** Aggregates per-call timings into one row per stage, in pipeline order. */
export function aggregateStageTimings(timings: StageTiming[]): StageRow[] {
  const byStage = new Map<string, StageRow>()
  for (const timing of timings) {
    const existing = byStage.get(timing.stage)
    if (existing) {
      existing.totalMs += timing.duration_ms
      existing.calls += 1
    } else {
      byStage.set(timing.stage, { stage: timing.stage, totalMs: timing.duration_ms, calls: 1 })
    }
  }
  return [...byStage.values()]
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

export function AgentExecutionTimeline({ timings }: { timings: StageTiming[] }) {
  const { t } = useTranslation()

  const { rows, totalMs, slowestMs } = useMemo(() => {
    const aggregated = aggregateStageTimings(timings)
    return {
      rows: aggregated,
      totalMs: aggregated.reduce((sum, row) => sum + row.totalMs, 0),
      // The bar is scaled against the slowest stage rather than the total,
      // so the shape stays readable when one stage dominates (LLM
      // inference routinely is 90%+ of a run).
      slowestMs: aggregated.reduce((max, row) => Math.max(max, row.totalMs), 0),
    }
  }, [timings])

  if (rows.length === 0) return null

  return (
    <Expander
      title={`⏱️ ${t('sql.executionTimeline')} (${rows.length} ${
        rows.length === 1 ? t('sql.stage') : t('sql.stages')
      }, ${formatDuration(totalMs)})`}
    >
      <ul className="flex flex-col gap-1.5 text-xs" aria-label={t('sql.executionTimeline')}>
        {rows.map((row) => {
          const share = slowestMs > 0 ? (row.totalMs / slowestMs) * 100 : 0
          return (
            <li key={row.stage} className="flex items-center gap-2">
              <span className="w-40 shrink-0 truncate" title={row.stage}>
                {formatStageName(row.stage)}
              </span>
              {/* Decorative: the number beside it carries the same
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
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-xs text-[var(--muted-foreground)]">{t('sql.executionTimelineHint')}</p>
    </Expander>
  )
}
