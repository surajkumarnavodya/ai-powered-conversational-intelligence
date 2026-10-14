import { AlertTriangle, CheckCircle2, CircleSlash, Clock, Unlink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import type { ArtifactFreshnessOut, ArtifactFreshnessState } from '@/lib/types'

/** Says, in words, how far an artifact's result can be trusted.
 *
 * The state is **always the server's** (`artifacts/staleness.py`), never
 * re-derived here. A second opinion about freshness is exactly how the
 * same artifact comes to read "current" on one surface and "dated" on
 * another -- and the server is the only side that can see the schema
 * fingerprint, the source turn and the refresh history.
 *
 * Every state renders as text with an icon, never as colour alone, and
 * the reasons are rendered as fixed translated sentences chosen by the
 * server's own reason codes -- never a raw error string.
 */

const TONES: Record<ArtifactFreshnessState, 'success' | 'warning' | 'danger' | 'neutral'> = {
  current: 'success',
  aging: 'warning',
  stale: 'danger',
  source_missing: 'neutral',
  no_result_captured: 'neutral',
}

const ICONS: Record<ArtifactFreshnessState, typeof CheckCircle2> = {
  current: CheckCircle2,
  aging: Clock,
  stale: AlertTriangle,
  source_missing: Unlink,
  no_result_captured: CircleSlash,
}

/** The reason codes `artifacts.models.StalenessReason` defines. Anything
 * the server adds later that is not listed here is skipped rather than
 * shown as a raw code -- an unexplained token in the UI is worse than one
 * fewer line of explanation. */
const KNOWN_REASONS = new Set([
  'aged_past_budget',
  'aged_past_stale_budget',
  'source_turn_deleted',
  'no_result_captured',
  'schema_changed',
  'result_changed_on_refresh',
  'last_refresh_failed',
  'database_not_available',
])

export function ArtifactFreshnessBadge({ freshness }: { freshness: ArtifactFreshnessOut }) {
  const { t } = useTranslation()
  const Icon = ICONS[freshness.state] ?? CircleSlash
  return (
    <Badge
      tone={TONES[freshness.state] ?? 'neutral'}
      data-artifact-freshness={freshness.state}
      className="shrink-0"
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {t(`artifacts.freshness.${freshness.state}`)}
    </Badge>
  )
}

/** Why it is not current -- or, for a refreshed artifact, what the refresh
 * found. Every reason the server reported is shown; they accumulate
 * because "why is this dated" usually has more than one answer. */
export function ArtifactFreshnessReasons({ freshness }: { freshness: ArtifactFreshnessOut }) {
  const { t } = useTranslation()
  const reasons = freshness.reasons.filter((reason) => KNOWN_REASONS.has(reason))
  if (reasons.length === 0) return null
  return (
    <ul className="flex flex-col gap-0.5 text-xs text-[var(--muted-foreground)]">
      {reasons.map((reason) => (
        <li key={reason}>{t(`artifacts.reason.${reason}`)}</li>
      ))}
    </ul>
  )
}
