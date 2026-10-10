import { CheckCircle2, CircleSlash, FileText, ShieldCheck, Sparkles } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { TruthLevel } from '@/lib/types'

/** What kind of backing a claim shown to the user actually has.
 *
 * Deliberately **orthogonal to `TruthLevel`**, not a replacement for it --
 * the same split `briefing/models.py`'s `ClaimClass` already makes against
 * `agent.provenance.DataTruthLevel` on the server. `TruthLevel` answers
 * "how sure are we"; this answers "what is behind this specific claim, as
 * the reader sees it". Two of the five states cannot be expressed as a
 * `TruthLevel` at all:
 *
 * - `source_backed` -- a document, policy or web answer resting on real
 *   citations. There is no `TruthLevel` for "a cited external source", and
 *   calling it `database_fact` would claim this platform computed it.
 * - `unavailable` -- there is no evidence. Silence here is the dangerous
 *   case, because an unlabelled claim reads as an endorsed one.
 *
 * The other three map 1:1 onto `TruthLevel`, and this module is the single
 * owner of their label text and tone: `TruthLevelBadge` delegates here
 * rather than keeping a second copy, so "Computed from data" is defined in
 * exactly one place.
 */
export type EvidenceStatus =
  | 'computed'
  | 'approved'
  | 'estimate'
  | 'source_backed'
  | 'unavailable'

const STATUSES: Record<
  EvidenceStatus,
  { label: string; tone: 'success' | 'warning' | 'accent' | 'neutral'; icon: typeof CheckCircle2 }
> = {
  computed: { label: 'Computed from data', tone: 'success', icon: CheckCircle2 },
  approved: { label: 'Approved definition', tone: 'accent', icon: ShieldCheck },
  estimate: { label: 'AI estimate', tone: 'warning', icon: Sparkles },
  source_backed: { label: 'Source-backed', tone: 'accent', icon: FileText },
  unavailable: { label: 'Not available', tone: 'neutral', icon: CircleSlash },
}

/** One sentence per status, for the expandable detail. Kept here beside the
 * labels so a label and its explanation cannot drift apart. */
const EXPLANATIONS: Record<EvidenceStatus, string> = {
  computed: 'Calculated from the rows this query returned.',
  approved: 'Uses a metric definition a reviewer approved and published.',
  estimate: 'Produced by this platform, not measured. Treat it as an estimate.',
  source_backed: 'Taken from the cited source documents or pages below.',
  unavailable: 'No supporting evidence was recorded for this claim.',
}

export function evidenceStatusLabel(status: EvidenceStatus): string {
  return STATUSES[status].label
}

export function evidenceStatusExplanation(status: EvidenceStatus): string {
  return EXPLANATIONS[status]
}

/** The three `TruthLevel`s this vocabulary shares with the server.
 *
 * Exhaustive over `TruthLevel` on purpose: a new level added to
 * `agent.provenance.DataTruthLevel` fails to type-check here rather than
 * silently falling back to a wrong label.
 */
const FROM_TRUTH_LEVEL: Record<TruthLevel, EvidenceStatus> = {
  database_fact: 'computed',
  confirmed_business_truth: 'approved',
  ai_inference: 'estimate',
}

export function evidenceStatusForTruthLevel(level: TruthLevel): EvidenceStatus {
  return FROM_TRUTH_LEVEL[level]
}

export function EvidenceBadge({
  status,
  truthLevel,
  className,
}: {
  status: EvidenceStatus
  /** Set only when this badge came from a server-reported `TruthLevel`.
   * Stamped as `data-truth-level`, which is an asserted contract in
   * `AnalyticsSummary.test.tsx` and `DecisionCenter.test.tsx` -- a
   * `source_backed`/`unavailable` badge has no truth level and correctly
   * carries no such attribute. */
  truthLevel?: TruthLevel
  className?: string
}) {
  const { label, tone, icon: Icon } = STATUSES[status]
  return (
    <Badge
      tone={tone}
      data-evidence-status={status}
      {...(truthLevel ? { 'data-truth-level': truthLevel } : {})}
      title={EXPLANATIONS[status]}
      className={className}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
      {label}
    </Badge>
  )
}
