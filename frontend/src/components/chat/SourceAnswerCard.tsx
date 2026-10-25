import { ChevronRight, Download } from 'lucide-react'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Markdown } from '@/components/ui/markdown'
import { downloadDocument } from '@/lib/api'
import { isUrl, uniqueSources } from '@/lib/citations'
import { sourceAnswerEvidenceStatus } from '@/lib/evidence'
import { hasOwnNumericMarkers, withNumberedCitations } from '@/lib/inlineCitations'
import type { SourceAnswer } from '@/lib/types'
import { SourcesSection } from './SourcesSection'

/** A document / policy / web answer.
 *
 * The layout is the answer first and everything else subordinate to it:
 *
 * ```
 * <the answer, with inline [1] [2] markers>
 * Sources (5)                      <- collapsed, the one canonical list
 * Source-backed · 5 sources · Show evidence
 * ```
 *
 * This replaced a card that led with a source-type heading, wrapped the
 * answer in a claim/evidence disclosure whose open state spilled five
 * full URLs above the fold, and then printed `Sources: 5` underneath --
 * so the same pages were named twice in this component alone, and a third
 * time by the lineage panel below it. The answer itself was the smallest
 * thing on screen.
 *
 * **The evidence status is still always rendered**, including
 * `unavailable`. That was the point of the component it replaces and it
 * has not changed: an unlabelled answer reads as an endorsed one, and an
 * answer whose supporting material was withheld (a policy chunk with a
 * `sensitivity_category`, a document whose `restricted_roles` exclude
 * this caller) must say "Not available" rather than borrow the
 * credibility of a source it is not allowed to show. What changed is only
 * how much room saying so takes.
 *
 * The source-type heading ("Web", "Documents") is gone from here. It was
 * originally dropped because `SourcesUsedChips` stated the same thing at
 * the foot of the turn; that chip row has since been removed too, as a
 * third copy of a fact already carried twice. What states it now is the
 * `Sources (N)` list -- real pages with their domains -- and the
 * provenance panel, which names the source kind and how it was retrieved.
 * One owner per fact (CLAUDE.md §4.8), and the owner is no longer a
 * label repeating a category.
 */
export function SourceAnswerCard({ result }: { sourceKey?: string; result: SourceAnswer }) {
  const { t } = useTranslation()
  const [showEvidence, setShowEvidence] = useState(false)
  const evidenceId = useId()

  const sources = uniqueSources(result.citations)
  const downloadable = sources.filter(
    (citation) => citation.has_pdf_bytes && !isUrl(citation.filename),
  )
  const status = sourceAnswerEvidenceStatus(result)

  // Sources that have something to *show* as evidence, paired with the
  // marker number the answer cites them by -- the same numbering
  // `SourcesSection` and the inline `[n]` markers use, so all three agree.
  const withExcerpts = sources
    .map((citation, index) => ({ citation, number: index + 1 }))
    .filter(({ citation }) => (citation.excerpt ?? '').trim().length > 0)
  // The evidence control is offered only when opening it would show
  // something. Without excerpts there is nothing to reveal that the
  // Sources list above does not already say, and a disclosure that opens
  // onto a restatement is how the duplication started.
  const hasEvidence = withExcerpts.length > 0

  // Long inline publisher links become `[n]` markers pointing at the list
  // below. Skipped when the model wrote its own numeric markers, whose
  // numbering this cannot verify (see lib/inlineCitations.ts).
  const answerText = hasOwnNumericMarkers(result.answer)
    ? result.answer
    : withNumberedCitations(result.answer, result.citations).text

  return (
    <div className="flex flex-col gap-2 text-sm">
      <Markdown>{answerText}</Markdown>

      {sources.length > 0 && <SourcesSection citations={result.citations} />}

      {/* The compact evidence line. One row, muted, below the answer --
          where the old card had a badge, a full-width explanation
          sentence, a "Backed by N cited sources." line and the URL list
          itself, all above the fold. */}
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-[var(--muted-foreground)]">
        <span
          data-evidence-status={status}
          className={
            status === 'source_backed' ? 'text-[var(--accent)]' : 'text-[var(--warning)]'
          }
        >
          {status === 'source_backed'
            ? t('evidence.sourceBacked')
            : t('evidence.notAvailable')}
        </span>
        {/* No source count here. `Sources (N)` sits directly above and
            already states it; a second count beside it was one of the
            duplicated facts this card is meant to stop producing. */}
        {hasEvidence && (
          <>
            <span aria-hidden="true">·</span>
            <button
              type="button"
              onClick={() => setShowEvidence((value) => !value)}
              aria-expanded={showEvidence}
              aria-controls={evidenceId}
              className="inline-flex items-center gap-0.5 rounded hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
            >
              <ChevronRight
                className={`h-3 w-3 shrink-0 transition-transform ${showEvidence ? 'rotate-90' : ''}`}
                aria-hidden="true"
              />
              {showEvidence ? t('evidence.hide') : t('evidence.show')}
            </button>
          </>
        )}
      </div>

      {/* Supporting excerpts, not a second source list.

          This deliberately renders no links and no URLs: `Sources (N)`
          above is the one place a source is listed, and an earlier
          version of this disclosure re-rendered that whole list, which
          put the same five URLs on screen twice in one card. What the
          disclosure adds is the thing the list cannot -- the source's
          own words, next to the marker number the answer cites it by.

          Only sources that actually carry an excerpt appear. A citation
          without one contributes nothing here and is simply absent,
          rather than printed as an empty row with its URL. */}
      {showEvidence && hasEvidence && (
        <div
          id={evidenceId}
          className="flex flex-col gap-2 border-l-2 border-[var(--border)] pl-3 text-xs text-[var(--muted-foreground)]"
        >
          <p>{t('evidence.inlineMarkerHint')}</p>
          {/* oxlint-disable-next-line jsx-a11y/no-redundant-roles */}
          <ol role="list" className="flex flex-col gap-2">
            {withExcerpts.map(({ citation, number }) => (
              <li key={number} className="flex gap-2">
                <span className="shrink-0 tabular-nums">[{number}]</span>
                {/* Plain text, never the markdown renderer: this is
                    retrieved third-party content, and rendering it as
                    markup would let a poisoned page inject links or
                    images into the answer area (CLAUDE.md §5.5). */}
                <q className="min-w-0 break-words italic">{citation.excerpt}</q>
              </li>
            ))}
          </ol>
        </div>
      )}

      {downloadable.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {downloadable.map((citation) => (
            <Button
              key={citation.document_id}
              size="sm"
              variant="secondary"
              onClick={() => void downloadDocument(citation.document_id, citation.filename)}
            >
              <Download className="h-3.5 w-3.5" />
              {t('common.download')} {citation.filename}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
