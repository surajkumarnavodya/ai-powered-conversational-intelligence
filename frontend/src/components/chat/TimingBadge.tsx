import { Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useElapsedSeconds } from '@/hooks/useElapsedSeconds'
import { cn } from '@/lib/utils'

type TimingBadgeProps =
  | { mode: 'pending'; startedAt: number; stage?: string | null }
  | { mode: 'done'; durationMs: number | null }

/** Perplexity/ChatGPT-style "Thinking Ns" -> "Answered in Ns" pill.
 *
 * While pending, the icon/label pulse (Tailwind's `animate-pulse`) and the
 * seconds counter ticks live -- a clear, continuously-updating signal that
 * the agent is still working, not stalled, addressing the "instead of a
 * static 'Loading' message, show it's actively in progress" request. Once
 * a turn resolves, its card switches to `mode: 'done'` with the actual
 * measured duration -- a plain, static, non-animated summary.
 *
 * `stage` (optional, pending only) is the graph node most recently
 * *completed*, from `POST /ask/stream`'s progress events. It is additive:
 * without it the badge renders exactly as it did before streaming
 * existed, which is what a deployment on an older server or behind a
 * buffering proxy gets.
 *
 * **It is not the node currently running, and this docstring used to say
 * it was.** `agent/nodes.py` calls `emit_progress` *after* a node
 * returns, with that node's measured `duration_ms`, so every event is a
 * completion. The practical consequence is the one that matters: measured
 * in Chrome 2026-10-30 (Phase 9, `UI-010`), this badge read
 * "plan query" for **97 seconds** while `generate_sql` ran -- 96,807 ms
 * against `plan_query`'s 0.05 ms. The longest wait in the run is labelled
 * with the previous node's name. Fixing it properly means a `started`
 * event per node, which changes the SSE contract, so it is flagged rather
 * than guessed at here; see docs/uat-readiness/phase-9-ui.md 5.2.
 */
export function TimingBadge(props: TimingBadgeProps) {
  const { t } = useTranslation()
  const liveSeconds = useElapsedSeconds(
    props.mode === 'pending' ? props.startedAt : 0,
    props.mode === 'pending',
  )
  if (props.mode === 'done' && props.durationMs === null) return null
  const seconds = props.mode === 'pending' ? liveSeconds : (props.durationMs ?? 0) / 1000

  return (
    <div
      className={cn(
        'inline-flex w-fit items-center gap-1.5 rounded-full bg-[var(--muted)] px-2.5 py-1 text-xs text-[var(--muted-foreground)]',
        props.mode === 'pending' && 'animate-pulse',
      )}
    >
      <Sparkles className="h-3.5 w-3.5" />
      {props.mode === 'pending' ? (
        <span>
          {t('common.thinking')} {seconds.toFixed(0)}s…
          {props.stage ? (
            // A fixed graph-node name, humanised for display. Rendered
            // only when `POST /ask/stream` actually reported one, so a
            // deployment without streaming (or behind a buffering proxy)
            // shows exactly the badge it always did rather than an empty
            // or guessed stage.
            <span className="ml-1 opacity-80">· {props.stage.replace(/_/g, ' ')}</span>
          ) : null}
        </span>
      ) : (
        <span>{t('common.answeredIn', { seconds: seconds.toFixed(1) })}</span>
      )}
    </div>
  )
}
