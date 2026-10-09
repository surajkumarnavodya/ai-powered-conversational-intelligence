import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TruthLevelBadge } from '@/components/analytics/TruthLevelBadge'
import {
  getExecutiveBriefing,
  type DecisionChain,
  type DecisionHeadline,
  type ExecutiveBriefing,
  type HealthIndicator,
  type MetricDefinition,
} from '@/lib/decisionCenterApi'
import { cn } from '@/lib/utils'

/** The Executive Decision Center (Prompt 52,
 * `52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`).
 *
 * One read-only screen over everything Prompts 33-50 record: business
 * health, KPI movement, anomalies, risks, recommendations, pending
 * decisions, actions and outcomes -- each headline expandable to its own
 * evidence, metric definition, period, source, confidence and lineage.
 *
 * **Presentation only.** Every number, every truth level and every
 * ordering decision arrives from `GET /decision-center/briefing`
 * already resolved. This component computes nothing, re-ranks nothing
 * and hides nothing: section order and the per-section cap come from the
 * server's own role-based profile, so the page cannot personalize
 * differently from what the server recorded it did.
 *
 * **Accessibility.** Each section is a labelled `region`; each headline
 * is a native `<button>` with `aria-expanded`/`aria-controls` over its
 * own drill-down panel, so the whole page is operable from the keyboard
 * and every expandable control announces its state. Truth levels are
 * carried by `TruthLevelBadge` (text, not colour alone), and the chain
 * stages use text markers rather than relying on colour to distinguish
 * reached from unreached.
 */

const SEVERITY_CLASS: Record<string, string> = {
  high: 'bg-[var(--destructive-soft)] text-[var(--destructive)]',
  medium: 'bg-[var(--accent-soft)] text-[var(--accent)]',
  low: 'bg-[var(--muted)] text-[var(--muted-foreground)]',
}

function HealthCard({ indicator }: { indicator: HealthIndicator }) {
  const { t } = useTranslation()
  const direction =
    indicator.direction === 'higher_is_better'
      ? t('decisionCenter.higherIsBetter')
      : indicator.direction === 'lower_is_better'
        ? t('decisionCenter.lowerIsBetter')
        : ''
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-[var(--muted-foreground)]">{indicator.label}</p>
        <TruthLevelBadge level={indicator.truth_level} />
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-[var(--foreground)]">
        {indicator.value}
        {indicator.unit === 'percent' ? '%' : ''}
      </p>
      {direction ? (
        <p className="mt-1 text-[11px] uppercase tracking-wide text-[var(--muted-foreground)]">
          {direction}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-[var(--muted-foreground)]">{indicator.detail}</p>
    </div>
  )
}

function DrillDown({
  headline,
  definition,
}: {
  headline: DecisionHeadline
  definition: MetricDefinition | undefined
}) {
  const { t } = useTranslation()
  const p = headline.provenance
  return (
    <div className="border-t border-[var(--border)] bg-[var(--muted)]/40 px-4 py-3 text-xs">
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        <div>
          <dt className="font-semibold text-[var(--foreground)]">
            {t('decisionCenter.source')}
          </dt>
          <dd className="text-[var(--muted-foreground)]">{p.source}</dd>
        </div>
        <div>
          <dt className="font-semibold text-[var(--foreground)]">
            {t('decisionCenter.period')}
          </dt>
          <dd className="text-[var(--muted-foreground)]">{p.period}</dd>
        </div>
        <div>
          <dt className="font-semibold text-[var(--foreground)]">
            {t('decisionCenter.confidence')}
          </dt>
          <dd className="text-[var(--muted-foreground)]">
            {p.confidence === null ? t('decisionCenter.notRecorded') : p.confidence.toFixed(2)}
          </dd>
        </div>
        <div>
          <dt className="font-semibold text-[var(--foreground)]">
            {t('decisionCenter.recordedAt')}
          </dt>
          <dd className="text-[var(--muted-foreground)]">{p.recorded_at}</dd>
        </div>
      </dl>

      <h4 className="mt-3 font-semibold text-[var(--foreground)]">
        {t('decisionCenter.metricDefinition')}
      </h4>
      {definition ? (
        <p className="text-[var(--muted-foreground)]">
          <span className="font-medium">{definition.business_name}</span>
          {definition.description ? ` — ${definition.description}` : ''}{' '}
          <span className="opacity-80">
            ({definition.status} v{definition.version})
          </span>
        </p>
      ) : (
        <p className="text-[var(--muted-foreground)]">
          {p.metric_definition_available
            ? t('decisionCenter.definitionUnresolved')
            : t('decisionCenter.noMetricDefinition')}
        </p>
      )}

      <h4 className="mt-3 font-semibold text-[var(--foreground)]">
        {t('decisionCenter.evidence')}
      </h4>
      {headline.evidence.length === 0 ? (
        <p className="text-[var(--muted-foreground)]">{t('decisionCenter.noEvidence')}</p>
      ) : (
        <ul className="space-y-1">
          {headline.evidence.map((item, index) => (
            <li key={`${item.label}-${index}`} className="flex flex-wrap items-center gap-2">
              <TruthLevelBadge level={item.truth_level} />
              <span className="font-medium text-[var(--foreground)]">{item.label}:</span>
              <span className="text-[var(--muted-foreground)]">{item.value}</span>
            </li>
          ))}
        </ul>
      )}

      {p.limitations.length > 0 ? (
        <>
          <h4 className="mt-3 font-semibold text-[var(--foreground)]">
            {t('decisionCenter.limitations')}
          </h4>
          <ul className="list-disc space-y-1 pl-5 text-[var(--muted-foreground)]">
            {p.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </>
      ) : null}

      <p className="mt-3 text-[var(--muted-foreground)]">
        {p.lineage_available
          ? t('decisionCenter.lineageAvailable')
          : t('decisionCenter.lineageUnavailable')}
      </p>
    </div>
  )
}

function HeadlineRow({
  headline,
  definition,
}: {
  headline: DecisionHeadline
  definition: MetricDefinition | undefined
}) {
  const [open, setOpen] = useState(false)
  const panelId = `drill-${headline.headline_id}`
  return (
    <li className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)]">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-inset"
      >
        <span
          className={cn(
            'mt-0.5 shrink-0 rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase',
            SEVERITY_CLASS[headline.severity_tier] ?? SEVERITY_CLASS.low,
          )}
        >
          {headline.severity_tier}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-[var(--foreground)]">
            {headline.title}
          </span>
          {headline.detail ? (
            <span className="mt-0.5 block text-xs text-[var(--muted-foreground)]">
              {headline.detail}
            </span>
          ) : null}
        </span>
        <TruthLevelBadge level={headline.truth_level} />
      </button>
      {open ? (
        <div id={panelId}>
          <DrillDown headline={headline} definition={definition} />
        </div>
      ) : null}
    </li>
  )
}

function ChainView({ chain }: { chain: DecisionChain }) {
  const { t } = useTranslation()
  return (
    <li className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
      <ol className="space-y-2">
        {chain.links.map((link) => (
          <li key={link.stage} className="flex flex-wrap items-start gap-2 text-xs">
            {/* A text marker, not colour alone -- the stage's state must
                be readable without seeing the styling. */}
            <span
              className={cn(
                'shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold',
                link.present
                  ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                  : 'bg-[var(--muted)] text-[var(--muted-foreground)]',
              )}
            >
              {link.present ? t('decisionCenter.stageReached') : t('decisionCenter.stageMissing')}
            </span>
            <span className="w-32 shrink-0 font-medium text-[var(--foreground)]">
              {link.stage}
            </span>
            {link.link_type ? (
              <span
                className="shrink-0 rounded bg-[var(--muted)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]"
                title={
                  link.link_type === 'declared'
                    ? t('decisionCenter.declaredHint')
                    : t('decisionCenter.correlatedHint')
                }
              >
                {link.link_type}
              </span>
            ) : null}
            <span className="min-w-0 flex-1 text-[var(--muted-foreground)]">
              {link.present ? link.summary : link.absent_reason}
            </span>
          </li>
        ))}
      </ol>
      {!chain.is_complete ? (
        <p className="mt-2 text-xs text-[var(--muted-foreground)]">
          {t('decisionCenter.chainIncomplete')}
        </p>
      ) : null}
    </li>
  )
}

export default function DecisionCenter() {
  const { t } = useTranslation()
  const [briefing, setBriefing] = useState<ExecutiveBriefing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getExecutiveBriefing()
      .then((result) => {
        if (!cancelled) setBriefing(result)
      })
      .catch(() => {
        if (!cancelled) setError(t('decisionCenter.loadFailed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [t])

  if (loading) {
    return (
      <p className="p-6 text-sm text-[var(--muted-foreground)]" role="status">
        {t('decisionCenter.loading')}
      </p>
    )
  }
  if (error || !briefing) {
    return (
      <p className="p-6 text-sm text-[var(--destructive)]" role="alert">
        {error ?? t('decisionCenter.loadFailed')}
      </p>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-4 sm:p-6">
      <header>
        <h1 className="text-xl font-semibold text-[var(--foreground)]">
          {t('decisionCenter.title')}
        </h1>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          {t('decisionCenter.subtitle', { profile: briefing.profile_id })}
        </p>
      </header>

      <section aria-labelledby="dc-health">
        <h2 id="dc-health" className="mb-3 text-sm font-semibold text-[var(--foreground)]">
          {t('decisionCenter.businessHealth')}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {briefing.health.map((indicator) => (
            <HealthCard key={indicator.key} indicator={indicator} />
          ))}
        </div>
      </section>

      {briefing.chains.length > 0 ? (
        <section aria-labelledby="dc-chains">
          <h2 id="dc-chains" className="mb-1 text-sm font-semibold text-[var(--foreground)]">
            {t('decisionCenter.chains')}
          </h2>
          <p className="mb-3 text-xs text-[var(--muted-foreground)]">
            {t('decisionCenter.chainsHint')}
          </p>
          <ul className="space-y-3">
            {briefing.chains.map((chain) => (
              <ChainView key={chain.chain_id} chain={chain} />
            ))}
          </ul>
        </section>
      ) : null}

      {briefing.sections
        .filter((section) => section.section !== 'business_health')
        .map((section) => (
          <section key={section.section} aria-labelledby={`dc-${section.section}`}>
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2
                id={`dc-${section.section}`}
                className="text-sm font-semibold text-[var(--foreground)]"
              >
                {section.title}
              </h2>
              {section.headlines.length < section.total_available ? (
                <span className="text-xs text-[var(--muted-foreground)]">
                  {t('decisionCenter.showingOf', {
                    shown: section.headlines.length,
                    total: section.total_available,
                  })}
                </span>
              ) : null}
            </div>
            {section.headlines.length === 0 ? (
              <p className="text-sm text-[var(--muted-foreground)]">{section.empty_reason}</p>
            ) : (
              <ul className="space-y-2">
                {section.headlines.map((headline) => (
                  <HeadlineRow
                    key={headline.headline_id}
                    headline={headline}
                    definition={
                      headline.provenance.metric_concept_key
                        ? briefing.metric_definitions[headline.provenance.metric_concept_key]
                        : undefined
                    }
                  />
                ))}
              </ul>
            )}
          </section>
        ))}

      {briefing.unavailable_sources.length > 0 ? (
        <section aria-labelledby="dc-unavailable">
          <h2 id="dc-unavailable" className="mb-1 text-sm font-semibold text-[var(--foreground)]">
            {t('decisionCenter.notIncluded')}
          </h2>
          <p className="mb-2 text-xs text-[var(--muted-foreground)]">
            {t('decisionCenter.notIncludedHint')}
          </p>
          <ul className="list-disc space-y-1 pl-5 text-xs text-[var(--muted-foreground)]">
            {briefing.unavailable_sources.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
