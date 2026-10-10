import { ChevronRight } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { sanitizeLabel } from '@/lib/analyticsCharts'
import { cn } from '@/lib/utils'
import type { TruthLevel } from '@/lib/types'
import {
  EvidenceBadge,
  evidenceStatusExplanation,
  evidenceStatusForTruthLevel,
  type EvidenceStatus,
} from './EvidenceBadge'

/** One claim, its evidence status, and its evidence behind a disclosure.
 *
 * The shape the brief asks for:
 *
 * ```
 * Revenue increased 18%
 * Computed from data
 * ```
 *
 * **One renderer, used wherever a claim is shown**: the insight narrative
 * (`TurnCard`), a document/policy/web answer (`SourceAnswerCard`) and each
 * recommendation (`EvidencePanel`). Three separate implementations of
 * "claim + badge + expandable detail" would have drifted on exactly the
 * thing that must not drift -- whether a claim without evidence still
 * looks endorsed.
 *
 * **The status is always rendered, including `unavailable`.** An
 * unlabelled claim reads as an endorsed one, so "Not available" is a
 * visible state rather than the absence of a badge. This is the whole
 * reason the component takes a required `status` instead of an optional
 * one.
 *
 * **The default open state is the caller's, because the right default
 * differs by context.** In the dense answer area the insight's evidence
 * starts collapsed, so readability is unchanged. Inside the analytics
 * expander, a panel literally titled "Recommendations and evidence"
 * starts open: that evidence was always visible before this component
 * existed, and collapsing it would have hidden working behaviour behind
 * a second layer of disclosure. Either way the claim text and its status
 * badge stay visible at every state, so the status can never be the
 * thing that is hidden. Details use plain `useState` rather than the shared
 * `Expander` (a `<details>` element) because the summary row here is a
 * claim with a badge beside it, not a title -- putting interactive badge
 * markup inside a `<summary>` is both awkward and worse for assistive
 * technology than an explicit button with `aria-expanded`/`aria-controls`.
 *
 * **Shows no model reasoning.** Everything rendered is either the claim
 * text the server already returned, a fixed explanation string owned by
 * `EvidenceBadge`, or caller-supplied evidence nodes built from
 * server-returned fields. There is no path here for a prompt, an
 * intermediate generation, or a chain of thought.
 */
export function ClaimEvidence({
  claim,
  status,
  /** Short line under the claim, e.g. what was verified. Already-safe
   * app-authored text; caller-supplied data must be sanitized by the
   * caller before it gets here. */
  summary,
  /** The evidence itself. Omit when there is genuinely none to show --
   * the disclosure is then not rendered at all, rather than opening onto
   * an empty box. */
  details,
  defaultOpen = false,
  className,
}: {
  claim: ReactNode
  status: EvidenceStatus
  summary?: string
  details?: ReactNode
  /** See the note above on why this is the caller's choice. */
  defaultOpen?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  const detailsId = useId()

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className="text-sm">{claim}</div>
      <div className="flex flex-wrap items-center gap-2">
        <EvidenceBadge status={status} />
        {details && (
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls={detailsId}
            className="inline-flex items-center gap-0.5 rounded text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          >
            <ChevronRight
              className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-90')}
              aria-hidden="true"
            />
            {open ? 'Hide evidence' : 'Evidence'}
          </button>
        )}
      </div>
      {/* Rendered only when open: a hidden-but-present panel would put
          evidence text into the page for a reader who did not ask for it,
          and the surrounding answer is already dense. */}
      {details && open && (
        <div id={detailsId} className="flex flex-col gap-1 border-l-2 border-[var(--border)] pl-3 text-xs">
          <p className="text-[var(--muted-foreground)]">{evidenceStatusExplanation(status)}</p>
          {summary && <p>{summary}</p>}
          {details}
        </div>
      )}
    </div>
  )
}

/** A plain list of evidence items, each with its own status badge.
 *
 * The multi-source case: a claim can rest on several pieces of evidence
 * at different levels at once (a recommendation grounded on both a
 * computed figure and an approved metric definition), and collapsing
 * those into the claim's single status would lose exactly the
 * distinction requirement 6 asks for.
 */
export function EvidenceList({
  items,
}: {
  /** Takes the server's own `TruthLevel`, not a pre-derived
   * `EvidenceStatus`: every evidence item in this codebase comes from a
   * `ProvenancedClaim`, and keeping the level lets each badge carry
   * `data-truth-level` -- an asserted contract in
   * `AnalyticsSummary.test.tsx` and `DecisionCenter.test.tsx`. Deriving
   * the status at the call site silently dropped that attribute, which
   * is how this signature was arrived at. */
  items: Array<{ level: TruthLevel; text: string }>
}) {
  if (items.length === 0) return null
  return (
    <ul className="flex flex-col gap-1" aria-label="Evidence">
      {items.map((item, index) => (
        <li key={`${index}-${item.text}`} className="flex flex-wrap items-center gap-2">
          <EvidenceBadge status={evidenceStatusForTruthLevel(item.level)} truthLevel={item.level} />
          <span>{sanitizeLabel(item.text, 200)}</span>
        </li>
      ))}
    </ul>
  )
}
