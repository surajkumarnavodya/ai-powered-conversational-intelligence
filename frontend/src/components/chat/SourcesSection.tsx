import { ChevronRight, ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { downloadDocument } from '@/lib/api'
import { isUrl, sourceDomain, sourceLabel, uniqueSources } from '@/lib/citations'
import type { Citation } from '@/lib/types'

/** The one canonical source list for an answer: `Sources (5)`, collapsed.
 *
 * Every other surface that wants to talk about this answer's sources
 * (the evidence disclosure, the provenance panel) points at this section
 * rather than rendering its own copy. Before this existed the same five
 * URLs appeared three times in one turn -- in the evidence panel, as a
 * bare count, and again under "Pages cited" in the lineage panel -- which
 * is both noisy and a standing invitation for two of them to disagree.
 *
 * Each source appears once, keyed by `canonicalSourceKey`, and is shown
 * as its real title with the domain beside it. A source with no reported
 * title falls back to its domain; nothing here derives a headline from a
 * URL slug (see `lib/citations.ts` on why that would be fabrication).
 *
 * Numbering matches `lib/inlineCitations.ts`, so the `[2]` in the prose
 * is the second row here -- the citation-to-claim mapping stays intact
 * rather than becoming two independently-ordered lists.
 */
export function SourcesSection({
  citations,
  defaultOpen = false,
}: {
  citations: readonly Citation[]
  /** Collapsed by default. The answer is the thing being read; the list
   * is for checking it. */
  defaultOpen?: boolean
}) {
  const { t } = useTranslation()
  const sources = uniqueSources(citations)
  if (sources.length === 0) return null

  return (
    <details className="group" open={defaultOpen}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-xs text-[var(--muted-foreground)] marker:content-none hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]">
        <ChevronRight
          className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90"
          aria-hidden="true"
        />
        {t('sources.sectionTitle', { count: sources.length })}
      </summary>
      {/* `role="list"` is not redundant: Tailwind preflight removes the
          marker and WebKit then drops list semantics, so VoiceOver stops
          announcing this as a list. Same note as
          ConversationContextPanel.tsx. */}
      {/* oxlint-disable-next-line jsx-a11y/no-redundant-roles */}
      <ol role="list" className="mt-1.5 flex flex-col gap-1.5">
        {sources.map((citation, index) => (
          <li
            key={`${index}-${citation.filename}`}
            className="flex gap-2 text-xs leading-5"
            data-source-index={index + 1}
          >
            <span className="shrink-0 tabular-nums text-[var(--muted-foreground)]">
              {index + 1}.
            </span>
            <SourceRow citation={citation} />
          </li>
        ))}
      </ol>
    </details>
  )
}

/** One row: the source's name, linked where there is somewhere to go.
 *
 * A web citation's `filename` is the page URL, so it opens in a new tab.
 * A stored document with PDF bytes downloads instead -- there is no
 * external URL for it. One with neither is plain text, because there is
 * genuinely nothing to link to. */
function SourceRow({ citation }: { citation: Citation }) {
  const { t } = useTranslation()
  const label = sourceLabel(citation)
  const domain = sourceDomain(citation)
  const pageSuffix = citation.page_number != null ? ` (p. ${citation.page_number})` : ''

  if (isUrl(citation.filename)) {
    return (
      <span className="min-w-0">
        <a
          href={citation.filename}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-baseline gap-1 text-[var(--accent)] underline underline-offset-2 hover:opacity-80"
        >
          <span className="break-words">{label}</span>
          <ExternalLink className="h-3 w-3 shrink-0 self-center" aria-hidden="true" />
        </a>
        {/* Shown only when it adds something the label does not already
            say -- with no title the label *is* the domain, and printing
            it twice is the duplication this component exists to remove. */}
        {domain && domain !== label && (
          <span className="ml-1.5 text-[var(--muted-foreground)]">{domain}</span>
        )}
      </span>
    )
  }

  if (citation.has_pdf_bytes) {
    return (
      <button
        type="button"
        onClick={() => void downloadDocument(citation.document_id, citation.filename)}
        className="min-w-0 break-words text-left text-[var(--accent)] underline underline-offset-2 hover:opacity-80"
      >
        {label}
        {pageSuffix}
        <span className="sr-only"> — {t('common.download')}</span>
      </button>
    )
  }

  return (
    <span className="min-w-0 break-words">
      {label}
      {pageSuffix}
    </span>
  )
}
