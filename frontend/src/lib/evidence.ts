import type { EvidenceStatus } from '@/components/analytics/EvidenceBadge'
import type { Citation, SourceAnswer } from './types'

/** Pure derivations of a claim's evidence status from server-returned
 * fields. Kept out of the components so each one is directly testable and
 * so the same question is never answered two different ways in two
 * different panels.
 */

/** What was actually verified about the insight narrative.
 *
 * `insight_numbers_checked` is the count of numbers in the stored insight,
 * every one of which `agent.insight.is_insight_grounded` matched against
 * the result summary before the insight was stored (an insight with an
 * unsupported number is dropped server-side and never reaches the client).
 * So this reports a real check, not a reassurance.
 *
 * `null` means the server did not report a count -- an older turn, or one
 * reconstructed from saved chat history. The sentence then says nothing
 * about verification rather than implying it happened.
 */
export function insightEvidenceSummary(
  numbersChecked: number | null,
  rowCount: number | null,
): string | undefined {
  if (numbersChecked === null) return undefined
  const against = rowCount === null ? 'the returned result' : `the ${rowCount} returned rows`
  if (numbersChecked === 0) {
    return `This summary states no figures of its own; it describes ${against}.`
  }
  const plural = numbersChecked === 1 ? 'figure' : 'figures'
  return `${numbersChecked} ${plural} in this summary were checked against ${against}.`
}

/** Evidence status for a document / policy / web answer.
 *
 * `source_backed` requires real citations. A source answer with none is
 * `unavailable`, and that is a genuine, reachable case rather than a
 * defensive branch:
 *
 * - `rag/graph.py`'s insufficient-information fallback answers without
 *   citing anything.
 * - A policy chunk carrying a `sensitivity_category`, or a document whose
 *   `restricted_roles` exclude this caller, is never summarized into an
 *   answer and yields `citations == []` (see CLAUDE.md's document/policy
 *   RAG section -- the download button's absence already follows from the
 *   same emptiness).
 *
 * Both must read "Not available", never "Source-backed": claiming a
 * source for an answer whose supporting material was withheld would turn
 * an access control into a false endorsement.
 */
export function sourceAnswerEvidenceStatus(result: SourceAnswer): EvidenceStatus {
  return result.citations.length > 0 ? 'source_backed' : 'unavailable'
}

/** De-duplicated citations, by document id where there is one.
 *
 * Extracted from `SourceAnswerCard`'s own inline logic so the card's
 * citation list and its evidence summary count the same things -- stating
 * "3 sources" beside a list of 2 would be its own small lie.
 */
export function uniqueCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>()
  return citations.filter((citation) => {
    const key = citation.document_id || citation.filename
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/** One line naming how many distinct sources back a source answer. */
export function sourceEvidenceSummary(citations: Citation[]): string {
  const count = uniqueCitations(citations).length
  if (count === 0) return 'No source was cited for this answer.'
  return count === 1 ? 'Backed by 1 cited source.' : `Backed by ${count} cited sources.`
}
