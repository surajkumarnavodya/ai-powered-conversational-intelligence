import type { TruthLevel } from '@/lib/types'
import { EvidenceBadge, evidenceStatusForTruthLevel } from './EvidenceBadge'

/** Prompt 30 -- every analytics claim shows its truth level, never implied.
 * Master rules 9-10: a computed value (database_fact), this platform's own
 * suggestion (ai_inference), and a human-approved definition
 * (confirmed_business_truth) are different kinds of claim and must look
 * different wherever they appear.
 *
 * Now a thin adapter over `EvidenceBadge`, which owns the label text and
 * tone for all five evidence statuses. Before, this file held its own copy
 * of three of those labels; with "Source-backed" and "Not available" added
 * for claims a `TruthLevel` cannot describe, two components would have
 * rendered "Computed from data" from two separate tables. The ~12 existing
 * call sites are unchanged -- they still pass a `TruthLevel`, which is the
 * right input for a panel whose level comes straight from the server.
 */
export function TruthLevelBadge({
  level,
  className,
}: {
  level: TruthLevel
  className?: string
}) {
  return (
    <EvidenceBadge
      status={evidenceStatusForTruthLevel(level)}
      truthLevel={level}
      className={className}
    />
  )
}
