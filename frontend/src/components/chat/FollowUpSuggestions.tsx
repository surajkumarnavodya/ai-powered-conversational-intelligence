import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { sanitizeLabel } from '@/lib/analyticsCharts'
import type { Recommendation } from '@/lib/types'

const MAX_QUESTION_CHARS = 200

/** A chip is a chip, not a sentence. Mirrors
 * `recommendation.followups.MAX_LABEL_CHARS`, which is where a label this
 * long is refused server-side -- this cap is the display backstop for a
 * recommendation produced before that rule existed (the forecast and
 * scenario engines both emit a full sentence and no short `action`). */
const MAX_LABEL_CHARS = 24

/** How many chips are shown. More than this stops being a suggestion and
 * starts being a second navigation surface. Mirrors
 * `Settings.max_follow_up_suggestions`, which caps it server-side; this
 * is the display backstop, not the only limit. */
const MAX_SUGGESTIONS = 4

export interface FollowUpChip {
  /** The short chip text -- what the reader scans. */
  label: string
  /** The full question a click submits. */
  question: string
}

/** One-click follow-up questions, directly under the answer.
 *
 * These come from the server's own recommendation engine
 * (`recommendation.engine`, `kind === 'next_question'`) -- nothing here
 * invents a question, and nothing here builds SQL. A chip carries
 * natural language only; clicking it re-enters the ordinary `POST /ask`
 * path, so `agent.input_guard`, the governance request policy and the
 * SQL validator all apply to it exactly as they would to a typed
 * question. Asking is also not running: the answer still requires an
 * explicit "Confirm and Run" before anything executes for the user, so
 * a click can never execute a query on its own.
 *
 * **Label vs. question.** `recommendation.followups` puts the short,
 * imperative core in `action` ("Top 5", "Show trend", "By Region") and
 * the full question in `claim.value` ("Top 5 Product Category by Sales
 * Amount"). That split is what lets the chip stay scannable while what
 * gets asked stays unambiguous. A recommendation with no `action` --
 * every pre-existing `next_question` producer, i.e.
 * `analytics.forecasting` and `analytics.scenario`, neither of which is
 * wired into this path today -- falls back to using its question as its
 * own label, so it still renders rather than silently vanishing.
 *
 * **Every string goes through `sanitizeLabel`** before it is displayed or
 * fed back into `onAsk` -- the same rule `lib/analyticsCharts` applies to
 * every database-originated label. To be precise about what that buys:
 * it replaces control characters, collapses whitespace and caps the
 * length. It does *not* strip markup, and does not need to -- React
 * escapes text on render, and the `/ask` path re-guards the text anyway.
 * The value here is that a recommendation derived from database text
 * cannot smuggle in a newline-spliced instruction or an unbounded label.
 *
 * Deduplicated by both label and question: the server already drops a
 * repeat (including two rules proposing the same column under different
 * names), and this is the display-side backstop for anything that
 * reaches the client anyway, since showing the same chip twice looks
 * like a bug.
 */
export function selectFollowUpQuestions(recommendations: Recommendation[]): FollowUpChip[] {
  const seenQuestions = new Set<string>()
  const seenLabels = new Set<string>()
  const chips: FollowUpChip[] = []
  for (const recommendation of recommendations) {
    if (recommendation.kind !== 'next_question') continue
    const question = sanitizeLabel(recommendation.claim.value, MAX_QUESTION_CHARS).trim()
    if (!question) continue
    const label = sanitizeLabel(recommendation.action ?? question, MAX_LABEL_CHARS).trim()
    if (!label) continue
    const questionKey = question.toLowerCase()
    const labelKey = label.toLowerCase()
    if (seenQuestions.has(questionKey) || seenLabels.has(labelKey)) continue
    seenQuestions.add(questionKey)
    seenLabels.add(labelKey)
    chips.push({ label, question })
    if (chips.length >= MAX_SUGGESTIONS) break
  }
  return chips
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
  const chips = useMemo(() => selectFollowUpQuestions(recommendations), [recommendations])

  if (chips.length === 0) return null

  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-medium text-[var(--muted-foreground)]">
        {t('followUps.title')}
      </p>
      <ul className="flex flex-wrap gap-2" aria-label={t('followUps.title')}>
        {chips.map((chip) => (
          <li key={chip.question}>
            {onAsk ? (
              <button
                type="button"
                disabled={disabled}
                onClick={() => onAsk(chip.question)}
                /* The chip reads short; the accessible name is the whole
                 * question, so a screen-reader user is never asked to
                 * act on "By Region" without knowing what it asks. The
                 * same text is the hover title for a sighted user. */
                aria-label={chip.question}
                title={chip.question}
                className="rounded-full border border-[var(--border)] px-3 py-1 text-left text-sm transition-colors hover:bg-[var(--muted)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {chip.label}
              </button>
            ) : (
              <span
                title={chip.question}
                className="rounded-full border border-[var(--border)] px-3 py-1 text-sm"
              >
                {chip.label}
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-[var(--muted-foreground)]">{t('followUps.hint')}</p>
    </div>
  )
}
