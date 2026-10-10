import { Layers } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Expander } from '@/components/ui/expander'
import {
  buildConversationHistory,
  MAX_FOLLOWUP_EXCHANGES,
  type QueryHistoryEntry,
} from '@/lib/history'
import { collapseQuestionWhitespace } from '@/lib/questionLabel'

const MAX_QUESTION_CHARS = 90

/** What context the next question will actually carry.
 *
 * A long conversation raises a fair question the UI never answered: when
 * I ask "and by region?", what does the agent know? The answer was
 * already computable on the client -- `buildConversationHistory` is the
 * exact function that builds the `conversation_history` sent with every
 * `/ask` call -- but it was never shown, so the follow-up window was
 * invisible and its limits surprising.
 *
 * **Calls that same function, rather than re-deriving the window.** If
 * the cap or the "succeeded turns only" rule ever changes, this panel
 * changes with it; there is no second definition of what gets sent.
 *
 * Shows the questions and the tables, not the SQL: the point is "what is
 * being carried", and a SQL dump here would duplicate what each turn
 * already shows in its own editor.
 */
export function ConversationContextPanel({ entries }: { entries: QueryHistoryEntry[] }) {
  const { t } = useTranslation()

  const { carried, tables, skipped } = useMemo(() => {
    const exchanges = buildConversationHistory(entries)
    const tableNames = [...new Set(exchanges.flatMap((exchange) => exchange.tables))].sort()
    // Turns that exist but are not carried: everything that did not
    // succeed, plus anything beyond the window.
    return {
      carried: exchanges,
      tables: tableNames,
      skipped: entries.length - exchanges.length,
    }
  }, [entries])

  // Nothing to explain until there is a prior turn to carry.
  if (carried.length === 0) return null

  return (
    <Expander
      title={
        <span className="inline-flex items-center gap-1.5">
          <Layers className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t('context.title', { count: carried.length, max: MAX_FOLLOWUP_EXCHANGES })}
        </span>
      }
    >
      <div className="flex flex-col gap-2 text-xs">
        <p className="text-[var(--muted-foreground)]">{t('context.hint')}</p>
        <ol className="list-decimal space-y-1 pl-4" aria-label={t('context.carriedLabel')}>
          {carried.map((exchange, index) => (
            // Index is a stable key here: the list is a positional window
            // into the conversation, rebuilt whenever the history changes.
            // eslint-disable-next-line react/no-array-index-key
            <li key={index}>
              {collapseQuestionWhitespace(exchange.question).slice(0, MAX_QUESTION_CHARS)}
              {exchange.question.length > MAX_QUESTION_CHARS ? '…' : ''}
            </li>
          ))}
        </ol>
        {tables.length > 0 && (
          <p>
            <span className="text-[var(--muted-foreground)]">{t('context.tables')}: </span>
            <span className="font-mono">{tables.join(', ')}</span>
          </p>
        )}
        {skipped > 0 && <p className="text-[var(--muted-foreground)]">{t('context.skipped', { count: skipped })}</p>}
      </div>
    </Expander>
  )
}
