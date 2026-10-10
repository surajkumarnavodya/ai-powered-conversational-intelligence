import { AlertTriangle, CheckCircle2, Clock, Database, ShieldCheck, XCircle } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import type { QueryHistoryEntry } from '@/lib/history'
import type { AttemptRecord } from '@/lib/types'

/** One compact row telling the reader where this query stands.
 *
 * Before this, the same facts were spread across the turn: a cost notice
 * near the SQL box, a duration badge above the results, a truncation
 * flag inside the chart section, retry information in its own expander,
 * a cache header nobody surfaced. Each was correct and none of them
 * answered "is this result safe to rely on" at a glance.
 *
 * This component **adds no new data and makes no new decision** -- every
 * badge reads a field the server already returned. In particular it does
 * not re-validate the SQL or re-judge cost on the client: validation and
 * cost are server-side gates, and a client-side opinion about them would
 * be exactly the sort of duplicated, drift-prone security decision this
 * codebase avoids.
 *
 * Deliberately badges, not sentences: a status row is scanned, not read.
 * The longer prose (`cost_notice`, `low_confidence_notice`,
 * `restricted_field_notice`) stays where it already is, in context next
 * to the control it is about.
 */

type StatusTone = 'neutral' | 'success' | 'warning' | 'danger' | 'accent'

interface StatusItem {
  key: string
  tone: StatusTone
  label: string
  /** Rendered as the badge's title, for the detail a label cannot carry. */
  detail?: string
  icon: 'validated' | 'blocked' | 'ok' | 'warn' | 'time' | 'db'
}

const ICONS = {
  validated: ShieldCheck,
  blocked: XCircle,
  ok: CheckCircle2,
  warn: AlertTriangle,
  time: Clock,
  db: Database,
} as const

function formatDuration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`
}

/** Derives the status items from fields the server already returned.
 *
 * Exported for its own test: the mapping is the whole logic here, and
 * testing it directly is clearer than asserting on rendered badges.
 */
export function deriveStatusItems(
  entry: QueryHistoryEntry,
  t: (key: string, options?: Record<string, unknown>) => string,
): StatusItem[] {
  const state = entry.finalState
  const items: StatusItem[] = []

  // 1. Validation. The server rejected or accepted the SQL before this
  // UI ever saw it; `attempt_history` records which.
  const blocked = state.attempt_history.find(
    (attempt: AttemptRecord) => attempt.outcome !== 'succeeded' && !attempt.will_retry,
  )
  const attempts = state.attempt_history.length
  if (blocked) {
    items.push({
      key: 'validation',
      tone: 'danger',
      icon: 'blocked',
      label: t('status.blocked'),
      detail: blocked.outcome,
    })
  } else if (state.sql) {
    items.push({
      key: 'validation',
      tone: 'success',
      icon: 'validated',
      label: t('status.validated'),
      detail: t('status.validatedDetail'),
    })
  }

  // 2. Retries, only when there were any -- "1 attempt" is noise.
  if (attempts > 1) {
    items.push({
      key: 'attempts',
      tone: 'warning',
      icon: 'warn',
      label: t('status.attempts', { count: attempts }),
    })
  }

  // 3. Cost, when the server flagged it.
  if (state.cost_notice) {
    items.push({
      key: 'cost',
      tone: 'warning',
      icon: 'warn',
      label: t('status.costFlagged'),
      detail: state.cost_notice,
    })
  }

  // 4. Execution outcome. Only meaningful once Confirm and Run happened.
  if (entry.confirmedError) {
    items.push({
      key: 'execution',
      tone: 'danger',
      icon: 'blocked',
      label: t('status.executionFailed'),
      detail: entry.confirmedError,
    })
  } else if (entry.confirmedRows) {
    items.push({
      key: 'execution',
      tone: 'success',
      icon: 'ok',
      label: t('status.rows', { count: entry.confirmedRows.length }),
    })
    if (entry.confirmedTruncated) {
      items.push({
        key: 'truncated',
        tone: 'warning',
        icon: 'warn',
        label: t('status.truncated'),
        detail: t('status.truncatedDetail'),
      })
    }
    if (entry.confirmedDurationMs !== null) {
      items.push({
        key: 'duration',
        tone: 'neutral',
        icon: 'time',
        label: formatDuration(entry.confirmedDurationMs),
        detail: t('status.durationDetail'),
      })
    }
    if (entry.confirmedCacheStatus) {
      items.push({
        key: 'cache',
        tone: 'neutral',
        icon: 'db',
        label:
          entry.confirmedCacheStatus === 'hit' ? t('status.cacheHit') : t('status.cacheMiss'),
        detail: t('status.cacheDetail'),
      })
    }
  }

  // 5. Governance. The server protected values before this UI saw them.
  if (state.restricted_field_notice) {
    items.push({
      key: 'governed',
      tone: 'accent',
      icon: 'validated',
      label: t('status.governed'),
      detail: state.restricted_field_notice,
    })
  }

  return items
}

export function ExecutionStatusBar({ entry }: { entry: QueryHistoryEntry }) {
  const { t } = useTranslation()
  const items = useMemo(() => deriveStatusItems(entry, t), [entry, t])

  if (items.length === 0) return null

  return (
    <ul
      className="flex flex-wrap items-center gap-1.5"
      aria-label={t('status.title')}
    >
      {items.map((item) => {
        const Icon = ICONS[item.icon]
        return (
          <li key={item.key}>
            <Badge tone={item.tone} title={item.detail}>
              <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
              {item.label}
            </Badge>
          </li>
        )
      })}
    </ul>
  )
}
