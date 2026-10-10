import { sanitizeLabel } from '@/lib/analyticsCharts'
import type { Recommendation } from '@/lib/types'
import { AnalyticsPanel } from './AnalyticsPanel'
import { TruthLevelBadge } from './TruthLevelBadge'

/** Recommended actions for this result (Prompt 17) with their evidence.
 *
 * Every recommendation is an AI estimate -- labelled as one, with each
 * piece of evidence showing its own truth level, so a suggestion is never
 * read as a confirmed fact.
 *
 * `next_question` recommendations used to render here too, as chips. They
 * now render in `components/chat/FollowUpSuggestions`, directly under the
 * answer: this panel is lazy-loaded inside the analytics expander and
 * only reachable after Confirm and Run, which is the wrong place for a
 * one-click follow-up. There is exactly one renderer for them, not two.
 * The `onAsk` prop went with them: this panel no longer asks anything. */
export function EvidencePanel({ recommendations }: { recommendations: Recommendation[] }) {
  if (recommendations.length === 0) return null

  const actions = recommendations.filter((rec) => rec.kind === 'action')

  return (
    <AnalyticsPanel id="analytics-evidence" title="Recommendations and evidence" truthLevel="ai_inference">
      {actions.length > 0 && (
        <ul className="flex flex-col gap-3">
          {actions.map((rec, index) => (
            <li key={`${index}-${rec.claim.value}`} className="flex flex-col gap-2 rounded-md border border-[var(--border)] p-3">
              <p className="text-sm font-medium">{sanitizeLabel(rec.claim.value, 300)}</p>
              {rec.action && (
                <p className="text-sm">
                  <span className="text-[var(--muted-foreground)]">Suggested action: </span>
                  {sanitizeLabel(rec.action, 200)}
                </p>
              )}
              {rec.measurable_impact && (
                <p className="text-xs text-[var(--muted-foreground)]">{sanitizeLabel(rec.measurable_impact, 200)}</p>
              )}
              <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted-foreground)]">
                {rec.category && <span>{sanitizeLabel(rec.category, 40)}</span>}
                {rec.confidence !== null && <span>Confidence {Math.round(rec.confidence * 100)}%</span>}
                {rec.rule_or_model && <span className="font-mono">{sanitizeLabel(rec.rule_or_model, 80)}</span>}
              </div>
              {rec.evidence.length > 0 && (
                <div className="flex flex-col gap-1">
                  <p className="text-xs font-medium text-[var(--muted-foreground)]">Evidence</p>
                  <ul className="flex flex-col gap-1">
                    {rec.evidence.map((item, evidenceIndex) => (
                      <li key={`${evidenceIndex}-${item.value}`} className="flex flex-wrap items-center gap-2 text-xs">
                        <TruthLevelBadge level={item.level} />
                        <span>{sanitizeLabel(item.value, 200)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {rec.limitations.length > 0 && (
                <ul className="list-disc pl-5 text-xs text-[var(--muted-foreground)]">
                  {rec.limitations.map((limitation) => (
                    <li key={limitation}>{sanitizeLabel(limitation, 200)}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </AnalyticsPanel>
  )
}
