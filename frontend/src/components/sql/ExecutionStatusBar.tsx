import { AlertTriangle, Database, ShieldCheck } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import type { QueryHistoryEntry } from '@/lib/history'

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
 *
 * **Scope is a deliberate split with `SqlIntelligencePanel`.** That
 * panel's collapsed title is itself a status line (`SQL • 42 rows`) and
 * its metadata grid carries the statement's identity, attempts and
 * duration. Those facts are therefore *not* repeated here -- both
 * surfaces are visible at once, so stating the same thing twice would be
 * noise. What stays here is the set of always-visible flags about whether
 * the *result* can be relied on: truncation, cache freshness, and
 * governance having altered values. Those must not be hidden behind a
 * collapse.
 *
 * Whether the confirmed run succeeded at all is deliberately not here
 * either: `QueryLifecycle`'s `executed` stage owns it, is rendered
 * directly above this bar, and is equally always visible.
 */

type StatusTone = 'neutral' | 'success' | 'warning' | 'danger' | 'accent'

interface StatusItem {
  key: string
  tone: StatusTone
  label: string
  /** Rendered as the badge's title, for the detail a label cannot carry. */
  detail?: string
  icon: 'validated' | 'warn' | 'db'
}

const ICONS = {
  validated: ShieldCheck,
  warn: AlertTriangle,
  db: Database,
} as const

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

  // 1. Result-trust flags, only meaningful once Confirm and Run produced
  // rows. Whether that run failed at all is `QueryLifecycle`'s `executed`
  // stage; the row count, duration, validation state and attempt count
  // live in `SqlIntelligencePanel` -- see this module's own docstring.
  if (entry.confirmedRows) {
    if (entry.confirmedTruncated) {
      items.push({
        key: 'truncated',
        tone: 'warning',
        icon: 'warn',
        label: t('status.truncated'),
        detail: t('status.truncatedDetail'),
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

  // 2. Governance. The server protected values before this UI saw them.
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
