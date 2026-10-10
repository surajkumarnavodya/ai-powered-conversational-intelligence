import { sanitizeLabel } from '@/lib/analyticsCharts'
import type { Recommendation } from '@/lib/types'
import { AnalyticsPanel } from './AnalyticsPanel'
import { ClaimEvidence, EvidenceList } from './ClaimEvidence'

/** Recommended actions for this result (Prompt 17) with their evidence.
 *
 * Every recommendation is an AI estimate -- labelled as one, with each
 * piece of evidence showing its own status, so a suggestion is never read
 * as a confirmed fact.
 *
 * `next_question` recommendations used to render here too, as chips. They
 * now render in `components/chat/FollowUpSuggestions`, directly under the
 * answer: this panel is lazy-loaded inside the analytics expander and
 * only reachable after Confirm and Run, which is the wrong place for a
 * one-click follow-up. There is exactly one renderer for them, not two.
 * The `onAsk` prop went with them: this panel no longer asks anything.
 *
 * The claim row and its evidence disclosure are `ClaimEvidence`/
 * `EvidenceList`, shared with the insight narrative and with
 * document/policy/web answers, rather than this panel's own earlier
 * inline markup. Three implementations of "claim + status + evidence"
 * would have drifted on the one thing that must not drift: whether a
 * claim with no evidence still looks endorsed.
 *
 * **A recommendation's own evidence is frequently multi-level** -- a
 * computed figure plus an approved metric definition, say. Each item
 * keeps its own badge instead of being folded into the recommendation's
 * single `ai_inference` status, because "this estimate rests on an
 * approved definition" and "this estimate rests on another estimate" are
 * different things to a reader. (The server already refuses to attach
 * `ai_inference` evidence at all -- see `recommendation/models.py` -- so
 * in practice the mix is computed and approved.)
 */
export function EvidencePanel({ recommendations }: { recommendations: Recommendation[] }) {
  if (recommendations.length === 0) return null

  const actions = recommendations.filter((rec) => rec.kind === 'action')
  if (actions.length === 0) return null

  return (
    <AnalyticsPanel id="analytics-evidence" title="Recommendations and evidence" truthLevel="ai_inference">
      <ul className="flex flex-col gap-3">
        {actions.map((rec, index) => (
          <li
            key={`${index}-${rec.claim.value}`}
            className="flex flex-col gap-2 rounded-md border border-[var(--border)] p-3"
          >
            <ClaimEvidence
              status="estimate"
              // This panel's whole purpose is the evidence, and it was
              // always visible here before `ClaimEvidence` existed, so it
              // stays open -- still collapsible, never hidden by default.
              defaultOpen
              claim={<span className="font-medium">{sanitizeLabel(rec.claim.value, 300)}</span>}
              summary={
                rec.evidence.length === 0
                  ? 'No supporting evidence was recorded for this recommendation.'
                  : undefined
              }
              details={
                rec.evidence.length > 0 || rec.limitations.length > 0 ? (
                  <>
                    <EvidenceList
                      items={rec.evidence.map((item) => ({
                        level: item.level,
                        text: item.value,
                      }))}
                    />
                    {rec.limitations.length > 0 && (
                      <ul className="list-disc pl-5 text-[var(--muted-foreground)]">
                        {rec.limitations.map((limitation) => (
                          <li key={limitation}>{sanitizeLabel(limitation, 200)}</li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : undefined
              }
            />
            {rec.action && (
              <p className="text-sm">
                <span className="text-[var(--muted-foreground)]">Suggested action: </span>
                {sanitizeLabel(rec.action, 200)}
              </p>
            )}
            {rec.measurable_impact && (
              <p className="text-xs text-[var(--muted-foreground)]">
                {sanitizeLabel(rec.measurable_impact, 200)}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted-foreground)]">
              {rec.category && <span>{sanitizeLabel(rec.category, 40)}</span>}
              {rec.confidence !== null && <span>Confidence {Math.round(rec.confidence * 100)}%</span>}
              {rec.rule_or_model && <span className="font-mono">{sanitizeLabel(rec.rule_or_model, 80)}</span>}
            </div>
          </li>
        ))}
      </ul>
    </AnalyticsPanel>
  )
}
