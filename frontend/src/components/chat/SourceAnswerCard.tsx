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
 * The source-type heading ("Web", "Documents") is gone from here because
 * `SourcesUsedChips` already states which sources answered, at the foot
 * of the turn -- CLAUDE.md §4.8, one owner per fact.
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
        {sources.length > 0 && (
          <>
            <span aria-hidden="true">·</span>
            <span>{t('sources.countLabel', { count: sources.length })}</span>
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

      {/* Rendered only when asked for. Reuses the same canonical list
          rather than printing a second one: what the disclosure adds is
          the mapping -- which numbered source is which page -- not another
          copy of the URLs. */}
      {showEvidence && sources.length > 0 && (
        <div
          id={evidenceId}
          className="border-l-2 border-[var(--border)] pl-3 text-xs text-[var(--muted-foreground)]"
        >
          <p className="mb-1.5">{t('evidence.inlineMarkerHint')}</p>
          <SourcesSection citations={result.citations} defaultOpen />
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
