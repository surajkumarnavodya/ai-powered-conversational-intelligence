import { ChevronRight, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TruthLevelBadge } from '@/components/analytics/TruthLevelBadge'
import { buildLineage, type LineageSource, type LineageStep } from '@/lib/sourceLineage'
import type { AskResponse } from '@/lib/types'

/** Compact "Source & Lineage" panel for one answered question: how the
 * answer got from the question to the insight, and which sources
 * contributed at each step.
 *
 * Collapsed it is one line -- `Sources (3)`. Expanded it shows the
 * contributing sources as a single summary line (`Database • CompanyMaster
 * • Business Glossary`), then the
 * `Question -> Source -> Tables -> Query -> Result -> Insight` chain, then
 * each source as an individually inspectable row.
 *
 * Everything rendered here comes from `lib/sourceLineage.ts`, which reads
 * it straight off `AskResponse` -- see that module's docstring for what is
 * deliberately *not* derived (the database engine, SQL-referenced columns,
 * per-source timestamps) and what is withheld on purpose (internal ids,
 * full table DDL). This component adds no lineage of its own; it only
 * labels and lays out what that function returns.
 *
 * **"Individually inspectable when permitted"** needs no permission check
 * here, and deliberately does not get one. A source the caller may not use
 * never appears in `sources_used` (the router filters its own selection
 * through a permission check before any subgraph runs), and a value the
 * caller may not read is masked before the row leaves the server. Both
 * gaps are then disclosed by real server-authored notices
 * (`permission_denied_notice`, `restricted_field_notice`), which this panel
 * surfaces. Adding a frontend-only gate would imply a control the backend
 * does not enforce -- which this codebase explicitly treats as worse than
 * none (see `AuthGate.tsx`: "no UI-level restriction is a security
 * boundary anywhere in this codebase").
 *
 * Deliberately separate from `SourcesUsedPanel`, which renders each
 * source's *answer body*, and from `SqlIntelligencePanel`, which inspects
 * the SQL path's own query mechanics. This panel is the cross-source
 * provenance view neither of those provides: it is the only place a
 * document/web/media answer's provenance is inspectable at all. Table
 * names appear here; the full DDL stays in `SchemaContextPanel`, which
 * already owns it. */
export function SourceLineagePanel({ state }: { state: AskResponse }) {
  const { t } = useTranslation()
  const [openSource, setOpenSource] = useState<string | null>(null)
  const lineage = buildLineage(state)

  // Nothing contributed to this answer that we can honestly attribute --
  // a rejected or clarification-only turn. An empty panel would imply the
  // lineage was hidden rather than absent.
  if (lineage.sources.length === 0) return null

  // "Did this answer run a query at all?" -- asked of the lineage the
  // server reported, not of `sources_used`, so it stays correct for a
  // mixed SQL+web answer (which does have a query stage and should show
  // the chain).
  const hasQueryStage = lineage.steps.some(
    (step) => step.key === 'query' && step.state === 'present',
  )

  const sourceLabel = (source: LineageSource) => t(`lineage.source.${source.kind}`)
  const summaryLine = lineage.sources
    .flatMap((source) => [sourceLabel(source), ...(source.name ? [source.name] : [])])
    .join(' • ')

  return (
    <details className="group rounded-lg border border-[var(--border)] bg-[var(--card)]">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium marker:content-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]">
        <ChevronRight
          className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90"
          aria-hidden="true"
        />
        {t('lineage.collapsedTitle', { count: lineage.sources.length })}
      </summary>

      <div className="flex flex-col gap-3 border-t border-[var(--border)] p-3">
        {/* The expanded one-line roll-call the feature asks for. */}
        <p className="text-xs text-[var(--foreground)]">{summaryLine}</p>

        {/* The Question -> Source -> Tables -> Query -> Result -> Insight
            chain is shown only for an answer that actually has a query
            stage. For a web or document answer every stage after Source
            is struck through by definition -- there is no SQL, no result
            set and no insight -- so the chain was four greyed-out words
            telling the reader nothing about their answer, in the panel
            they opened to learn where it came from. It remains for the
            SQL path, where a missing stage is genuinely informative
            (a retry that never reached execution, say). */}
        {hasQueryStage && <LineageChain steps={lineage.steps} />}

        {/* `role="list"` is not redundant here: Tailwind preflight removes the
            list marker, and WebKit then drops list semantics, so VoiceOver on
            Safari/iOS stops announcing this as a list. See
            ConversationContextPanel.tsx for the full note. */}
        {/* oxlint-disable-next-line jsx-a11y/no-redundant-roles */}
        <ul role="list" className="flex flex-col gap-1.5">
          {lineage.sources.map((source) => {
            const isOpen = openSource === source.kind
            const panelId = `lineage-source-${source.kind}`
            return (
              <li key={source.kind} className="rounded-md border border-[var(--border)]">
                <button
                  type="button"
                  // A source with nothing further to show is not a button
                  // that does nothing -- it is simply not expandable.
                  disabled={source.details.length === 0}
                  aria-expanded={source.details.length === 0 ? undefined : isOpen}
                  aria-controls={source.details.length === 0 ? undefined : panelId}
                  onClick={() => setOpenSource(isOpen ? null : source.kind)}
                  className="flex w-full flex-wrap items-center gap-2 px-2.5 py-1.5 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] focus-visible:ring-inset disabled:cursor-default"
                >
                  <span className="font-medium">{sourceLabel(source)}</span>
                  {source.name && (
                    <span className="text-[var(--muted-foreground)]">{source.name}</span>
                  )}
                  <span className="text-[var(--muted-foreground)]">
                    {t(`lineage.retrieval.${source.retrieval}`)}
                  </span>
                  <span className="ml-auto flex items-center gap-1.5">
                    <TruthLevelBadge level={source.truthLevel} />
                    {source.details.length > 0 && (
                      <ChevronRight
                        className={`h-3.5 w-3.5 shrink-0 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                        aria-hidden="true"
                      />
                    )}
                  </span>
                </button>
                {isOpen && source.details.length > 0 && (
                  <dl
                    id={panelId}
                    className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t border-[var(--border)] px-2.5 py-1.5 text-xs"
                  >
                    {source.details.map((detail, index) => (
                      <div key={`${detail.label}-${index}`} className="col-span-2 grid grid-cols-subgrid">
                        <dt className="text-[var(--muted-foreground)]">
                          {t(`lineage.detail.${detail.label}`)}
                        </dt>
                        <dd className="min-w-0 break-words">{detail.value}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            )
          })}
        </ul>

        {/* That something was withheld belongs in a provenance view -- but
            the authoritative wording does not live here. `restricted_field_notice`
            and `permission_denied_notice` are already rendered next to the
            answer by `ExecutionStatusBar`, whose own docstring states it
            keeps them "in context"; repeating the server's sentence here
            would show the same text twice in one turn. So this is a short,
            fixed marker pointing at a restriction that exists, and the
            detail stays with its owner. */}
        {lineage.restrictionNotices.length > 0 && (
          <p className="flex items-start gap-1.5 text-xs text-[var(--warning)]">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('lineage.withheld', { count: lineage.restrictionNotices.length })}
          </p>
        )}

        {/* Model name, duration and the rest are diagnostics, not an
            answer. They were a permanently-visible row inside a panel
            that is itself about provenance, so every turn paid vertical
            space for "llama3.1:8b / 150752 ms" whether or not anyone
            cared. Now one more click, in a disclosure that says what it
            holds -- nothing is removed, it just stops being the last
            thing under every answer. */}
        {lineage.metadata.length > 0 && (
          <details className="group/diag">
            <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-xs text-[var(--muted-foreground)] marker:content-none hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]">
              <ChevronRight
                className="h-3 w-3 shrink-0 transition-transform group-open/diag:rotate-90"
                aria-hidden="true"
              />
              {t('lineage.diagnostics')}
            </summary>
            <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
              {lineage.metadata.map((item) => (
                <div key={item.label} className="flex gap-1.5">
                  <dt>{t(`lineage.detail.${item.label}`)}</dt>
                  <dd className="text-[var(--foreground)]">{item.value}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
    </details>
  )
}

/** The `Question -> Source -> Tables -> Query -> Result -> Insight` chain.
 *
 * An ordered list, so the sequence is conveyed structurally rather than by
 * the arrow glyphs alone (which are `aria-hidden`). A step that genuinely
 * does not apply -- tables and SQL for a web-only answer -- is shown struck
 * through and labelled, never dropped: a reader comparing two answers must
 * be able to see that one had no query stage at all. */
function LineageChain({ steps }: { steps: LineageStep[] }) {
  const { t } = useTranslation()
  return (
    <ol
      aria-label={t('lineage.chainLabel')}
      className="flex flex-wrap items-center gap-x-1 gap-y-1 text-xs"
    >
      {steps.map((step, index) => (
        <li key={step.key} className="flex items-center gap-1">
          {index > 0 && (
            <span aria-hidden="true" className="text-[var(--muted-foreground)]">
              →
            </span>
          )}
          <span
            data-lineage-step={step.key}
            data-lineage-state={step.state}
            className={
              step.state === 'present'
                ? 'rounded bg-[var(--accent-soft)] px-1.5 py-0.5 font-medium text-[var(--foreground)]'
                : 'rounded px-1.5 py-0.5 text-[var(--muted-foreground)] line-through'
            }
          >
            {t(`lineage.step.${step.key}`)}
            {step.summary && <span className="ml-1 font-normal">({step.summary})</span>}
          </span>
          {step.state !== 'present' && (
            <span className="sr-only">{t(`lineage.state.${step.state}`)}</span>
          )}
        </li>
      ))}
    </ol>
  )
}
