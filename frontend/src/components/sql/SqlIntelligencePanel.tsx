import { ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { QueryPlanContent } from '@/components/chat/QueryPlanPanel'
import { SchemaContextContent } from '@/components/chat/SchemaContextPanel'
import { Expander } from '@/components/ui/expander'
import type { AskResponse } from '@/lib/types'

/** One expander answering "how did the agent arrive at this SQL, and
 * against what".
 *
 * **A composition, not new rendering.** Every section below delegates to
 * the component that already owned it (`QueryPlanContent`,
 * `SchemaContextContent`); this adds the grouping and the lineage link.
 * Before, the same information sat in three or four sibling expanders
 * stacked under the answer, so a reader had to open each in turn to
 * assemble one picture, and a long conversation became a wall of
 * collapsed rows.
 *
 * The SQL *editor* deliberately stays outside this panel. It is an
 * action, not an explanation, and burying the Confirm-and-Run control
 * one expander deeper would make the main path harder to reach.
 *
 * **Shows only what the server returned.** The plan steps, the retrieved
 * table DDL, the chosen database and model, and the governing metric
 * names are all `AskResponse` fields. No prompt text, no model
 * reasoning, no intermediate generation.
 */
export function SqlIntelligencePanel({
  state,
  showDatabase,
}: {
  state: AskResponse
  /** True only when more than one database is configured -- naming the
   * database is noise in a single-database deployment. */
  showDatabase: boolean
}) {
  const { t } = useTranslation()

  const hasPlan = (state.query_plan?.length ?? 0) > 0
  const hasSchema = state.schema_tables.length > 0
  const hasMetrics = state.governing_metrics.length > 0
  const hasDetails = (showDatabase && Boolean(state.database)) || Boolean(state.model)

  if (!hasPlan && !hasSchema && !hasMetrics && !hasDetails) return null

  const sectionCount = [hasPlan, hasSchema, hasMetrics, hasDetails].filter(Boolean).length

  return (
    <Expander title={`🧠 ${t('sqlIntelligence.title')} (${sectionCount})`}>
      <div className="flex flex-col gap-4">
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

        {hasDetails && (
          <section className="flex flex-col gap-1">
            <h4 className="text-xs font-semibold">{t('details.title')}</h4>
            {showDatabase && state.database && (
              <p className="text-xs text-[var(--muted-foreground)]">
                {t('details.database')}: <strong>{state.database}</strong>
              </p>
            )}
            {state.model && (
              <p className="text-xs text-[var(--muted-foreground)]">
                {t('details.model')}: <strong>{state.model}</strong>
              </p>
            )}
          </section>
        )}

        {/* The lineage explorer is a separate, permission-gated screen
            (its own route re-checks access server-side). This is a plain
            link, not a deep link: that page resolves its own database
            selection, so pre-seeding it from here would imply state it
            does not read. */}
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
