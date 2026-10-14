import { useTranslation } from 'react-i18next'
import { ArtifactFreshnessReasons } from './ArtifactFreshnessBadge'
import { ResultsTable } from '@/components/sql/ResultsTable'
import { TruthLevelBadge } from '@/components/analytics/TruthLevelBadge'
import { Badge } from '@/components/ui/badge'
import { sanitizeLabel } from '@/lib/analyticsCharts'
import type { ArtifactDetailOut, ArtifactResultOut } from '@/lib/types'

/** One opened artifact: what it is, where it came from, and how far its
 * result can be trusted.
 *
 * Renders only fields the server returned. In particular it does not
 * re-derive freshness, does not reconstruct the SQL when the server
 * withheld it, and does not compute a row count of its own -- the counts
 * shown are the query's, which can exceed the stored rows because a
 * persisted snapshot is capped.
 *
 * The original capture is deliberately *not* rendered as a table here:
 * those rows already render in the conversation turn this artifact points
 * at, and "Go to question" is one click away. A refreshed result is shown,
 * because it exists nowhere else.
 */

function formatTimestamp(value: string | null): string | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString()
}

function ResultSummaryLine({
  label,
  result,
}: {
  label: string
  result: ArtifactResultOut | null
}) {
  const { t } = useTranslation()
  if (result === null) return null
  const captured = formatTimestamp(result.captured_at)
  return (
    <p className="text-xs text-[var(--muted-foreground)]">
      <span className="font-medium text-[var(--foreground)]">{label}</span>{' '}
      {result.row_count !== null && t('artifacts.rows', { count: result.row_count })}
      {captured ? ` • ${captured}` : ''}
      {result.truncated ? ` • ${t('artifacts.storedSubset', { count: result.returned_rows })}` : ''}
    </p>
  )
}

export function ArtifactDetail({ artifact }: { artifact: ArtifactDetailOut }) {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col gap-3 border-t border-[var(--border)] px-3 py-3 text-sm">
      {artifact.question && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
            {t('artifacts.sourceQuestion')}
          </p>
          {/* An explicit cap, not `sanitizeLabel`'s short default:
              truncating the source question would lose exactly what the
              artifact exists to keep. 300 matches the server's own title
              column width. */}
          <p>{sanitizeLabel(artifact.question, 300)}</p>
        </div>
      )}

      <ArtifactFreshnessReasons freshness={artifact.freshness} />

      {/* The literal answer to "is this based on an older result" -- shown
          as its own line rather than folded into the freshness badge,
          because a refreshed artifact is current *and* its original
          numbers have moved. Both facts matter. */}
      {artifact.result_changed_since_creation && (
        <p
          className="text-xs text-[var(--warning)]"
          data-artifact-result-changed="true"
          role="status"
        >
          {t('artifacts.resultChanged')}
        </p>
      )}

      <div className="flex flex-col gap-1">
        <ResultSummaryLine label={t('artifacts.originalResult')} result={artifact.origin_result} />
        <ResultSummaryLine label={t('artifacts.latestResult')} result={artifact.refreshed_result} />
        {artifact.last_refresh_outcome === 'failed' && artifact.last_refresh_error && (
          <p className="text-xs text-[var(--danger)]">
            {t('artifacts.refreshFailed', { reason: artifact.last_refresh_error })}
          </p>
        )}
      </div>

      {artifact.insight && (
        <div className="flex flex-col gap-1">
          <TruthLevelBadge level={artifact.truth_level} className="self-start" />
          <p>{artifact.insight}</p>
        </div>
      )}

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-[var(--muted-foreground)]">{t('artifacts.generated')}</dt>
        <dd>{formatTimestamp(artifact.created_at) ?? '—'}</dd>
        {artifact.database && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t('artifacts.database')}</dt>
            <dd>{artifact.database}</dd>
          </>
        )}
        {artifact.model && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t('artifacts.model')}</dt>
            <dd>{artifact.model}</dd>
          </>
        )}
        {artifact.tables_referenced.length > 0 && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t('artifacts.tables')}</dt>
            {/* Never `.map(sanitizeLabel)`: `Array.map` passes the index
                as the second argument, which `sanitizeLabel` reads as its
                character cap -- every label would be truncated to its own
                position in the list. */}
            <dd>{artifact.tables_referenced.map((name) => sanitizeLabel(name)).join(', ')}</dd>
          </>
        )}
        {artifact.governed_metrics.length > 0 && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t('artifacts.governedMetrics')}</dt>
            <dd>{artifact.governed_metrics.map((name) => sanitizeLabel(name)).join(', ')}</dd>
          </>
        )}
      </dl>

      {/* The SQL, when this caller may see it. `sql_withheld_reason` is the
          server's own fixed sentence -- there is no client-side decision
          here about who may read a query. */}
      {artifact.sql ? (
        <pre className="overflow-x-auto rounded border border-[var(--border)] bg-[var(--muted)] p-2 text-xs">
          <code>{artifact.sql}</code>
        </pre>
      ) : (
        artifact.sql_withheld_reason && (
          <Badge tone="neutral" className="self-start">
            {artifact.sql_withheld_reason}
          </Badge>
        )
      )}

      {/* Only the refreshed rows get a table: the original capture's rows
          already render in the turn this artifact points at. */}
      {artifact.refreshed_result && artifact.refreshed_result.rows.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
            {t('artifacts.latestResult')}
          </p>
          <ResultsTable
            columns={artifact.refreshed_result.columns}
            rows={artifact.refreshed_result.rows as unknown[][]}
          />
        </div>
      )}
    </div>
  )
}
