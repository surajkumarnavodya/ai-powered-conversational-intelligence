import { AlertTriangle, ExternalLink, Loader2, Play } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { QueryPlanContent } from '@/components/chat/QueryPlanPanel'
import { SchemaContextContent } from '@/components/chat/SchemaContextPanel'
import { SqlEditor } from '@/components/sql/SqlEditor'
import { Button } from '@/components/ui/button'
import { Expander } from '@/components/ui/expander'
import type { QueryHistoryEntry } from '@/lib/history'

/** Everything about *this* turn's SQL, behind one progressive disclosure.
 *
 * Collapsed it reads as a status line -- `SQL • Validated • 42 rows` --
 * so a long conversation stays scannable. Expanded it holds the SQL
 * itself, the Confirm and Run control, and the metadata that describes
 * it: statement type, referenced tables, database, duration.
 *
 * The attempt count deliberately is not here: `AgentExecutionTimeline`
 * states it in its own collapsed summary, where it is visible without
 * opening a panel, alongside which stage was actually retried.
 *
 * **It composes; it does not re-derive.** The SQL viewer is the existing
 * `SqlEditor` (CodeMirror with `@codemirror/lang-sql`, so syntax
 * highlighting and Copy SQL already come with it -- no new dependency and
 * no second code-presentation mechanism). The plan and schema sections
 * delegate to `QueryPlanContent`/`SchemaContextContent`. Statement type
 * and referenced tables come from `AskResponse.sql_summary`, parsed
 * server-side from the AST.
 *
 * **Safety properties, all structural rather than conventional:**
 * - The SQL shown is `entry.editableSql`, seeded from the server's own
 *   `state.sql`. Nothing here generates or rewrites SQL.
 * - Nothing here executes anything. The only action is Confirm and Run,
 *   which calls the store's `confirmAndRun`, which posts to
 *   `POST /execute` -- and that route re-validates and re-executes the
 *   *current* text server-side every time. An edit in this box is
 *   therefore exactly as untrusted as the original generation, and the
 *   panel cannot bypass validation.
 * - Copying is `navigator.clipboard.writeText` via the shared
 *   `CopyButton`; it never runs the query.
 * - Only server-returned fields are rendered. No prompt text, no model
 *   reasoning, no intermediate generation.
 *
 * **Open by default until the turn has a result**, collapsed afterwards.
 * Burying the primary action behind a collapse would make the main path
 * harder to reach; once a result exists, the summary line is the useful
 * state and the SQL is reference material.
 */

/** The collapsed summary: what this SQL produced, if anything yet.
 *
 * Deliberately not validation state or the failing stage -- those are
 * `QueryLifecycle`'s, rendered unconditionally above this panel.
 */
export function buildSqlSummaryLine(
  entry: QueryHistoryEntry,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const parts: string[] = [t('sqlIntelligence.sqlLabel')]

  // Validation state, the stage this query stopped at, and whether the
  // confirmed run failed are all `QueryLifecycle`'s -- it is rendered
  // just above this panel and is always visible, so repeating any of it
  // here would state the same fact twice. What stays is the artifact
  // itself: how much this SQL returned, or that it has not run yet.
  if (entry.confirmedRows) parts.push(t('status.rows', { count: entry.confirmedRows.length }))
  else if (!entry.confirmedError) parts.push(t('sqlIntelligence.notRun'))

  return parts.join(' • ')
}

interface MetadataRow {
  key: string
  label: string
  value: string
}

/** The metadata grid. Every value is a server-returned field. */
export function buildSqlMetadata(
  entry: QueryHistoryEntry,
  showDatabase: boolean,
  t: (key: string, options?: Record<string, unknown>) => string,
): MetadataRow[] {
  const state = entry.finalState
  const rows: MetadataRow[] = []

  if (state.sql_summary) {
    rows.push({
      key: 'statementType',
      label: t('sqlIntelligence.statementType'),
      value: state.sql_summary.statement_type,
    })
    if (state.sql_summary.referenced_tables.length > 0) {
      rows.push({
        key: 'referencedTables',
        label: t('sqlIntelligence.referencedTables'),
        value: state.sql_summary.referenced_tables.join(', '),
      })
    }
  }
  if (showDatabase && state.database) {
    rows.push({ key: 'database', label: t('details.database'), value: state.database })
  }
  if (state.model) {
    rows.push({ key: 'model', label: t('details.model'), value: state.model })
  }
  if (entry.confirmedRows) {
    rows.push({
      key: 'rowCount',
      label: t('sqlIntelligence.rowCount'),
      value: String(entry.confirmedRows.length),
    })
  }
  if (entry.confirmedDurationMs !== null) {
    rows.push({
      key: 'duration',
      label: t('sqlIntelligence.duration'),
      value:
        entry.confirmedDurationMs < 1000
          ? `${Math.round(entry.confirmedDurationMs)}ms`
          : `${(entry.confirmedDurationMs / 1000).toFixed(2)}s`,
    })
  }
  return rows
}

export function SqlIntelligencePanel({
  entry,
  showDatabase,
  onSqlChange,
  onConfirmAndRun,
  isConfirming = false,
  showEditor = true,
}: {
  entry: QueryHistoryEntry
  /** True only when more than one database is configured -- naming the
   * database is noise in a single-database deployment. */
  showDatabase: boolean
  onSqlChange: (sql: string) => void
  onConfirmAndRun: () => void
  isConfirming?: boolean
  /** False for a turn whose SQL cannot be re-run (no live handler), so the
   * SQL is still inspectable without offering a dead action. */
  showEditor?: boolean
}) {
  const { t } = useTranslation()
  const state = entry.finalState

  const summaryLine = useMemo(() => buildSqlSummaryLine(entry, t), [entry, t])
  const metadata = useMemo(() => buildSqlMetadata(entry, showDatabase, t), [entry, showDatabase, t])

  const hasPlan = (state.query_plan?.length ?? 0) > 0
  const hasSchema = state.schema_tables.length > 0
  const hasMetrics = state.governing_metrics.length > 0
  const hasSql = Boolean(entry.editableSql || state.sql)

  if (!hasSql && !hasPlan && !hasSchema && !hasMetrics && metadata.length === 0) return null

  const hasResult = Boolean(entry.confirmedRows) || Boolean(entry.confirmedError)
  // The SQL text is pristine only while the user has not edited it; a
  // cost or confidence notice describes the SQL the server produced, so
  // it is withheld once that text changes.
  const sqlIsServerSql = entry.editableSql === state.sql

  return (
    <Expander title={`🧠 ${summaryLine}`} defaultOpen={!hasResult}>
      <div className="flex flex-col gap-4">
        {hasSql && showEditor && (
          <section className="flex flex-col gap-2">
            <p className="text-xs text-[var(--muted-foreground)]">{t('sql.hint')}</p>
            <SqlEditor value={entry.editableSql} onChange={onSqlChange} />
            {state.cost_notice && sqlIsServerSql && (
              <p className="flex items-center gap-1.5 text-xs text-[var(--warning)]">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {state.cost_notice}
              </p>
            )}
            <div>
              <Button variant="primary" onClick={onConfirmAndRun} disabled={isConfirming}>
                {isConfirming ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {t('sql.confirmAndRun')}
              </Button>
            </div>
          </section>
        )}

        {hasSql && !showEditor && (
          <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-[var(--muted)] p-2 font-mono text-xs">
            {entry.editableSql || state.sql}
          </pre>
        )}

        {metadata.length > 0 && (
          <section>
            <h4 className="mb-1.5 text-xs font-semibold">{t('sqlIntelligence.metadata')}</h4>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {metadata.map((row) => (
                <div key={row.key} className="col-span-2 grid grid-cols-subgrid">
                  <dt className="text-[var(--muted-foreground)]">{row.label}</dt>
                  <dd className="min-w-0 break-words font-mono">{row.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {hasPlan && (
          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-semibold">{t('sql.queryPlan')}</h4>
            <QueryPlanContent plan={state.query_plan} />
          </section>
        )}

        {hasMetrics && (
          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-semibold">{t('sqlIntelligence.governedMetrics')}</h4>
            <p className="text-xs text-[var(--muted-foreground)]">
              {t('sqlIntelligence.governedMetricsHint')}
            </p>
            <ul className="flex flex-wrap gap-1.5">
              {state.governing_metrics.map((metric) => (
                <li
                  key={metric.business_name}
                  className="rounded-full border border-[var(--border)] px-2.5 py-0.5 text-xs"
                  title={metric.approved_expression ?? undefined}
                >
                  {metric.business_name}
                </li>
              ))}
            </ul>
          </section>
        )}

        {hasSchema && (
          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-semibold">{t('sql.schemaContext')}</h4>
            <p className="text-xs text-[var(--muted-foreground)]">
              {t('sqlIntelligence.schemaHint')}
            </p>
            <SchemaContextContent tables={state.schema_tables} />
          </section>
        )}

        {/* The lineage explorer is a separate, permission-gated screen
            whose own route re-checks access server-side. A plain link,
            not a deep link: that page resolves its own database
            selection, so pre-seeding it would imply state it does not
            read. */}
        <Link
          to="/data-lineage"
          className="inline-flex w-fit items-center gap-1.5 text-xs text-[var(--accent)] hover:underline"
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
          {t('sqlIntelligence.openLineage')}
        </Link>
      </div>
    </Expander>
  )
}
