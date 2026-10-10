import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { sanitizeLabel } from '@/lib/analyticsCharts'
import type { Recommendation } from '@/lib/types'

const MAX_QUESTION_CHARS = 200

/** How many chips are shown. More than this stops being a suggestion and
 * starts being a second navigation surface. */
const MAX_SUGGESTIONS = 4

/** One-click follow-up questions, directly under the answer.
 *
 * These come from the server's own recommendation engine
 * (`recommendation.engine`, `kind === 'next_question'`) -- nothing here
 * invents a question. They were already rendered before this component
 * existed, but **inside** `EvidencePanel`, which is lazy-loaded, nested
 * in the analytics expander, and only reachable after Confirm and Run.
 * A follow-up suggestion that the reader has to go looking for is not a
 * suggestion, so this is the single renderer now and `EvidencePanel`
 * keeps the half that matches its name: actions with their evidence.
 *
 * **Every label goes through `sanitizeLabel`** before it is displayed or
 * fed back into `onAsk` -- the same rule `lib/analyticsCharts` applies to
 * every database-originated label. To be precise about what that buys:
 * it replaces control characters, collapses whitespace and caps the
 * length. It does *not* strip markup, and does not need to -- React
 * escapes text on render, and a clicked chip goes back through the normal
 * `/ask` path, so `agent.input_guard`, the governance request policy and
 * the SQL validator all apply to it exactly as they would to anything the
 * user typed. The value here is that a recommendation derived from
 * database text cannot smuggle in a newline-spliced instruction or an
 * unbounded label.
 *
 * Deduplicated by the sanitized text: two rules can legitimately produce
 * the same question, and showing it twice looks like a bug.
 */
export function selectFollowUpQuestions(recommendations: Recommendation[]): string[] {
  const seen = new Set<string>()
  const questions: string[] = []
  for (const recommendation of recommendations) {
    if (recommendation.kind !== 'next_question') continue
    const question = sanitizeLabel(recommendation.claim.value, MAX_QUESTION_CHARS).trim()
    if (!question) continue
    const key = question.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    questions.push(question)
    if (questions.length >= MAX_SUGGESTIONS) break
  }
  return questions
}

export function FollowUpSuggestions({
  recommendations,
  onAsk,
  disabled = false,
}: {
  recommendations: Recommendation[]
  onAsk?: (question: string) => void
  /** True while another question is in flight, so a click cannot queue a
   * second run behind the first. */
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const questions = useMemo(() => selectFollowUpQuestions(recommendations), [recommendations])

  if (questions.length === 0) return null

  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-medium text-[var(--muted-foreground)]">
        {t('followUps.title')}
      </p>
      <ul className="flex flex-wrap gap-2" aria-label={t('followUps.title')}>
        {questions.map((question) => (
          <li key={question}>
            {onAsk ? (
              <button
                type="button"
                disabled={disabled}
                onClick={() => onAsk(question)}
                className="rounded-full border border-[var(--border)] px-3 py-1 text-left text-sm transition-colors hover:bg-[var(--muted)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {question}
              </button>
            ) : (
              <span className="rounded-full border border-[var(--border)] px-3 py-1 text-sm">
                {question}
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-[var(--muted-foreground)]">{t('followUps.hint')}</p>
    </div>
  )
}
