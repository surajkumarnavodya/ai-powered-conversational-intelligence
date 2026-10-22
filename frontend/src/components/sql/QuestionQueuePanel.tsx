import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  ShieldAlert,
  Timer,
  X,
  XCircle,
} from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { sanitizeLabel } from '@/lib/analyticsCharts'
import type { QuestionJob, QuestionJobStatus } from '@/lib/questionsApi'
import { isTerminalStatus } from '@/lib/questionsApi'
import { selectLiveCount, useQuestionQueueStore } from '@/store/questionQueueStore'

/**
 * Live view of this user's asynchronously submitted questions.
 *
 * **What this component does and does not own.** It renders the status of
 * each queued question and the queue's own capacity. It does **not**
 * render answers: a completed job's result is handed back to the normal
 * chat surface, which already owns rendering a result table, a chart, an
 * insight and citations. Re-rendering a result here would be a second
 * owner for every one of those facts (CLAUDE.md 4.8).
 *
 * **Nothing internal is exposed.** The badges show the job's status, the
 * source names the server reported, a coarse confidence indicator and,
 * where useful, the model name -- all values `AskResponse` already
 * returns to this browser. No routing rule, provider name, key, raw
 * prompt or system instruction appears here, because none of it is sent
 * to the client.
 *
 * **Every database- or server-derived string is sanitised before
 * display** (`sanitizeLabel`), as this codebase requires for any such
 * text -- including the question itself, which came back from the server
 * and is therefore treated like any other round-tripped value.
 */

/** Status -> icon, tone and i18n key. A single table rather than a chain
 * of ternaries, so adding the eighth status the server might one day
 * publish is one row and a TypeScript error if it is forgotten. */
const STATUS_PRESENTATION: Record<
  QuestionJobStatus,
  { tone: 'neutral' | 'accent' | 'success' | 'warning' | 'danger'; labelKey: string }
> = {
  queued: { tone: 'neutral', labelKey: 'questionQueue.status.queued' },
  running: { tone: 'accent', labelKey: 'questionQueue.status.running' },
  completed: { tone: 'success', labelKey: 'questionQueue.status.completed' },
  failed: { tone: 'danger', labelKey: 'questionQueue.status.failed' },
  cancelled: { tone: 'neutral', labelKey: 'questionQueue.status.cancelled' },
  timeout: { tone: 'warning', labelKey: 'questionQueue.status.timeout' },
  blocked_by_guardrail: {
    tone: 'warning',
    labelKey: 'questionQueue.status.blockedByGuardrail',
  },
}

function StatusIcon({ status }: { status: QuestionJobStatus }) {
  const className = 'h-3.5 w-3.5 shrink-0'
  switch (status) {
    case 'queued':
      return <Clock className={className} aria-hidden />
    case 'running':
      return <Loader2 className={`${className} animate-spin`} aria-hidden />
    case 'completed':
      return <CheckCircle2 className={className} aria-hidden />
    case 'timeout':
      return <Timer className={className} aria-hidden />
    case 'blocked_by_guardrail':
      return <ShieldAlert className={className} aria-hidden />
    case 'cancelled':
      return <XCircle className={className} aria-hidden />
    default:
      return <AlertTriangle className={className} aria-hidden />
  }
}

/** The highest-confidence source and a coarse band for it.
 *
 * Deliberately three bands with words, not a percentage. The underlying
 * number is the bounded sum of the deterministic signals that matched
 * (`agent/orchestrator/source_signals.py`) and is explicitly *not* a
 * calibrated probability -- rendering "0.83" would invite a reader to
 * treat it as an 83% chance the answer is right, which is a claim nobody
 * is making. An empty map means routing had nothing to choose between,
 * so nothing is shown rather than an invented "low".
 */
function confidenceBand(
  confidence: Record<string, number>,
): { source: string; band: 'high' | 'medium' | 'low' } | null {
  const entries = Object.entries(confidence)
  if (entries.length === 0) return null
  const [source, value] = entries.reduce((best, current) =>
    current[1] > best[1] ? current : best,
  )
  if (value <= 0) return null
  return { source, band: value >= 0.7 ? 'high' : value >= 0.4 ? 'medium' : 'low' }
}

function JobCard({ job }: { job: QuestionJob }) {
  const { t } = useTranslation()
  const cancel = useQuestionQueueStore((s) => s.cancel)
  const dismiss = useQuestionQueueStore((s) => s.dismiss)
  const presentation = STATUS_PRESENTATION[job.status]
  const terminal = isTerminalStatus(job.status)
  const confidence = confidenceBand(job.source_confidence)

  return (
    <li className="rounded-lg border border-border bg-card p-3 text-sm">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 flex-1 break-words font-medium text-card-foreground">
          {sanitizeLabel(job.question, 160)}
        </p>
        <button
          type="button"
          onClick={() => (terminal ? dismiss(job.job_id) : cancel(job.job_id))}
          className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={
            terminal
              ? t('questionQueue.dismiss')
              : t('questionQueue.cancel')
          }
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge tone={presentation.tone} className="gap-1">
          <StatusIcon status={job.status} />
          {t(presentation.labelKey)}
        </Badge>

        {job.status === 'queued' && job.queue_wait_seconds >= 1 && (
          <Badge tone="neutral">
            {t('questionQueue.waiting', { seconds: Math.round(job.queue_wait_seconds) })}
          </Badge>
        )}
        {job.run_seconds !== null && job.run_seconds >= 1 && (
          <Badge tone="neutral">
            {t('questionQueue.ran', { seconds: Math.round(job.run_seconds) })}
          </Badge>
        )}
        {job.attempts > 1 && (
          <Badge tone="warning">{t('questionQueue.attempts', { count: job.attempts })}</Badge>
        )}

        {/* Source badges -- the same source names the "Sources used" panel
            already shows, so the two never disagree. */}
        {job.sources_used.map((source) => (
          <Badge key={source} tone="accent">
            {sanitizeLabel(source, 24)}
          </Badge>
        ))}

        {confidence && (
          <Badge tone={confidence.band === 'high' ? 'success' : 'neutral'}>
            {t(`questionQueue.confidence.${confidence.band}`, {
              source: sanitizeLabel(confidence.source, 24),
            })}
          </Badge>
        )}

        {job.model_used && (
          <Badge tone="neutral">{sanitizeLabel(job.model_used, 32)}</Badge>
        )}
      </div>

      {/* A refusal and a failure read differently on purpose: one is the
          system working as configured, the other is something going
          wrong, and collapsing them would mislead the reader. */}
      {job.status === 'blocked_by_guardrail' && (
        <p className="mt-2 text-xs text-muted-foreground">
          {t('questionQueue.blockedExplanation')}
        </p>
      )}
      {job.error_message && job.status !== 'blocked_by_guardrail' && (
        <p className="mt-2 text-xs text-muted-foreground">
          {sanitizeLabel(job.error_message, 300)}
        </p>
      )}
    </li>
  )
}

export function QuestionQueuePanel() {
  const { t } = useTranslation()
  // Subscribed as the two stored slices and joined with `useMemo`, not
  // through a `selectJobs(state)` selector. A selector that builds an
  // array returns a new reference on every call, and zustand compares
  // selector results by reference -- so the subscription would see a
  // change on every render and loop until React's update-depth guard
  // fired. `byId` and `order` are only ever replaced when they actually
  // change, so subscribing to each is stable.
  const byId = useQuestionQueueStore((s) => s.byId)
  const order = useQuestionQueueStore((s) => s.order)
  const jobs = useMemo(
    () => order.map((id) => byId[id]).filter(Boolean),
    [order, byId],
  )
  // Safe as a selector: it returns a number, which compares by value.
  const liveCount = useQuestionQueueStore(selectLiveCount)
  const stats = useQuestionQueueStore((s) => s.stats)
  const availability = useQuestionQueueStore((s) => s.availability)
  const queueFullNotice = useQuestionQueueStore((s) => s.queueFullNotice)
  const submitError = useQuestionQueueStore((s) => s.submitError)
  const refresh = useQuestionQueueStore((s) => s.refresh)
  const stopPolling = useQuestionQueueStore((s) => s.stopPolling)

  useEffect(() => {
    // Rehydrate from the server on mount, which is also what makes a
    // page refresh safe: the jobs come back from the server with every
    // result re-governed for the caller's current roles, rather than from
    // a stale client-side copy.
    void refresh()
    // Stop the timer when this panel unmounts, so a background interval
    // does not keep polling for a view nobody is looking at.
    return () => stopPolling()
  }, [refresh, stopPolling])

  // A deployment whose auth mode cannot attribute a job to a user, or one
  // with the queue switched off, gets no asynchronous UI at all -- not an
  // error message about a feature it does not have.
  if (availability === 'unavailable') return null
  if (jobs.length === 0 && !queueFullNotice && !submitError) return null

  const atLimit =
    stats !== null &&
    stats.max_concurrent_per_user > 0 &&
    stats.running_for_caller >= stats.max_concurrent_per_user

  return (
    <section
      className="rounded-xl border border-border bg-background p-3"
      aria-label={t('questionQueue.title')}
    >
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground">
          {t('questionQueue.title')}
        </h2>
        {stats !== null && (
          <p className="text-xs text-muted-foreground">
            {t('questionQueue.capacity', {
              running: stats.running_for_caller,
              max: stats.max_concurrent_per_user,
              queued: stats.queued_for_caller,
            })}
          </p>
        )}
      </header>

      {/* Being at the concurrency limit is NOT an error: further questions
          queue rather than fail. Saying so plainly is the difference
          between a user waiting calmly and a user resubmitting. */}
      {atLimit && liveCount > 0 && (
        <p className="mb-2 rounded-md bg-muted px-2.5 py-1.5 text-xs text-muted-foreground">
          {t('questionQueue.atLimit', { max: stats?.max_concurrent_per_user })}
        </p>
      )}
      {queueFullNotice && (
        <p className="mb-2 rounded-md bg-muted px-2.5 py-1.5 text-xs text-foreground">
          {sanitizeLabel(queueFullNotice, 240)}
        </p>
      )}
      {submitError && (
        <p className="mb-2 rounded-md bg-muted px-2.5 py-1.5 text-xs text-foreground">
          {sanitizeLabel(submitError, 240)}
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {jobs.map((job) => (
          <JobCard key={job.job_id} job={job} />
        ))}
      </ul>
    </section>
  )
}
