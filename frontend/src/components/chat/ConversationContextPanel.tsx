import { Eraser, Layers, RotateCcw, Undo2, X } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { TruthLevelBadge } from '@/components/analytics/TruthLevelBadge'
import { Expander } from '@/components/ui/expander'
import {
  buildContextSnapshot,
  itemsByProvenance,
  type ContextItem,
  type ContextLifetime,
} from '@/lib/conversationContext'
import { MAX_FOLLOWUP_EXCHANGES, type QueryHistoryEntry } from '@/lib/history'
import { useChatStore } from '@/store/chatStore'

/** What context the next question will actually carry, and what scope the
 * conversation is currently working in -- with each item's provenance,
 * and controls to narrow it.
 *
 * A long conversation raises a fair question the UI never answered: when
 * I ask "and by region?", what does the agent know? The carried window
 * was already computable on the client, but was invisible, so its limits
 * were surprising and there was no way to act on them.
 *
 * **Renders `buildContextSnapshot` and adds nothing.** That function
 * builds the carried turns from `buildConversationHistory` -- the exact
 * function that builds the `conversation_history` request field -- with
 * the same exclusion set the store applies when asking, so this list
 * cannot drift from what is sent. Every other item is read straight off
 * the turn the server already returned. This component only labels,
 * groups and lays out.
 *
 * **Three things are deliberately not shown.** No system prompt, no
 * reasoning, and no SQL. The first two are never sent to the client at
 * all (`AskResponse` carries neither), so there is nothing here to leak;
 * SQL is omitted because every turn already shows its own in
 * `SqlIntelligencePanel`, and a second copy would be the same fact in
 * two places. The scope items are names and labels -- a table name, a
 * database name, an approved metric's business name, a filter's column
 * and value -- never DDL, never a connection detail, never row contents.
 *
 * **Removing context cannot bypass authorization, by construction.** The
 * only removable items are carried turns, and removal only ever *narrows*
 * `conversation_history` -- it cannot add a turn, edit one, or reorder
 * them (see `buildConversationHistory`). Nothing the server authorizes is
 * read from that field: permission checks read the caller's own roles,
 * the database is re-routed per question, and the schema, metrics and
 * plan are rebuilt per question. The server additionally treats the field
 * as untrusted, caller-resent text it normalizes, injection-scans and
 * caps (`agent.input_guard.sanitize_conversation_history`). So this panel
 * gives away nothing a caller could not already do by simply asking a
 * fresh question -- which is exactly what "clear context" is.
 */
export function ConversationContextPanel({ entries }: { entries: QueryHistoryEntry[] }) {
  const { t } = useTranslation()
  const excludedContextEntryIds = useChatStore((state) => state.excludedContextEntryIds)
  const toggleContextEntryExcluded = useChatStore((state) => state.toggleContextEntryExcluded)
  const clearConversationContext = useChatStore((state) => state.clearConversationContext)
  const startNewChat = useChatStore((state) => state.startNewChat)

  const snapshot = useMemo(
    () => buildContextSnapshot(entries, excludedContextEntryIds),
    [entries, excludedContextEntryIds],
  )
  const groups = useMemo(() => itemsByProvenance(snapshot), [snapshot])

  // Nothing to explain until a turn exists that could be carried. A
  // conversation of nothing but failures has no context, and an empty
  // panel would imply the context was hidden rather than absent.
  const hasAnythingToSay =
    snapshot.items.length > 0 || snapshot.excludedCount > 0 || snapshot.unsuccessfulCount > 0
  if (!hasAnythingToSay) return null

  return (
    <Expander
      title={
        <span className="inline-flex items-center gap-1.5">
          <Layers className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t('context.title', { count: snapshot.items.length })}
        </span>
      }
    >
      <div className="flex flex-col gap-3 text-xs">
        <p className="text-[var(--muted-foreground)]">
          {t('context.hint', { max: MAX_FOLLOWUP_EXCHANGES })}
        </p>

        {groups.map((group) => (
          <section key={group.provenance} className="flex flex-col gap-1.5">
            <h4 className="font-medium text-[var(--foreground)]">
              {t(`context.provenance.${group.provenance}`)}
            </h4>
            <p className="text-[var(--muted-foreground)]">
              {t(`context.provenanceHint.${group.provenance}`)}
            </p>
            <ul role="list" className="flex flex-col gap-1">
              {group.items.map((item) => (
                <ContextRow
                  key={item.id}
                  item={item}
                  onRemove={() => toggleContextEntryExcluded(item.id)}
                />
              ))}
            </ul>
          </section>
        ))}

        {/* Removed turns stay listed, and restorable. A removal that
            vanished without trace would be indistinguishable from a turn
            that was never carried, and would leave no way back. */}
        {snapshot.excludedCount > 0 && (
          <RemovedTurns
            entries={entries}
            excludedIds={excludedContextEntryIds}
            onRestore={toggleContextEntryExcluded}
          />
        )}

        <ul role="list" className="flex flex-col gap-0.5 text-[var(--muted-foreground)]">
          {snapshot.beyondWindowCount > 0 && (
            <li>{t('context.beyondWindow', { count: snapshot.beyondWindowCount })}</li>
          )}
          {snapshot.unsuccessfulCount > 0 && (
            <li>{t('context.unsuccessful', { count: snapshot.unsuccessfulCount })}</li>
          )}
        </ul>

        <div className="flex flex-wrap gap-2 border-t border-[var(--border)] pt-2">
          <button
            type="button"
            onClick={clearConversationContext}
            disabled={snapshot.carriedCount === 0}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2 py-1 font-medium hover:bg-[var(--accent-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Eraser className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('context.clear')}
          </button>
          <button
            type="button"
            onClick={startNewChat}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2 py-1 font-medium hover:bg-[var(--accent-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <RotateCcw className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('context.startFresh')}
          </button>
        </div>
        {/* Says plainly which of the two actions keeps the transcript, so
            neither is clicked expecting the other's effect. */}
        <p className="text-[var(--muted-foreground)]">{t('context.actionsHint')}</p>
      </div>
    </Expander>
  )
}

/** How plainly one item affects the next question.
 *
 * Carried by text, never by colour or position alone -- `influencing`
 * versus `carried` is a real difference in effect (only the most recent
 * carried turn is functionally read downstream), and a reader must not
 * have to infer it from list order. */
const LIFETIME_TONE: Record<ContextLifetime, string> = {
  influencing: 'text-[var(--foreground)]',
  carried: 'text-[var(--muted-foreground)]',
  rederived: 'text-[var(--muted-foreground)]',
}

function ContextRow({ item, onRemove }: { item: ContextItem; onRemove: () => void }) {
  const { t } = useTranslation()
  return (
    <li
      data-context-kind={item.kind}
      data-context-provenance={item.provenance}
      data-context-lifetime={item.lifetime}
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-[var(--border)] px-2 py-1"
    >
      <span className="text-[var(--muted-foreground)]">{t(`context.kind.${item.kind}`)}:</span>
      <span className={`min-w-0 break-words ${LIFETIME_TONE[item.lifetime]}`}>{item.value}</span>
      <span className="ml-auto flex items-center gap-1.5">
        <span className="text-[var(--muted-foreground)]">
          {t(`context.lifetime.${item.lifetime}`)}
        </span>
        {item.truthLevel && <TruthLevelBadge level={item.truthLevel} />}
        {item.removable && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={t('context.removeItem', { value: item.value })}
            className="rounded p-0.5 text-[var(--muted-foreground)] hover:bg-[var(--accent-soft)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      </span>
    </li>
  )
}

function RemovedTurns({
  entries,
  excludedIds,
  onRestore,
}: {
  entries: QueryHistoryEntry[]
  excludedIds: ReadonlySet<string>
  onRestore: (entryId: string) => void
}) {
  const { t } = useTranslation()
  const removed = entries.filter((entry) => excludedIds.has(entry.entryId))
  if (removed.length === 0) return null
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="font-medium text-[var(--foreground)]">
        {t('context.removedTitle', { count: removed.length })}
      </h4>
      <ul role="list" className="flex flex-col gap-1">
        {removed.map((entry) => (
          <li
            key={entry.entryId}
            data-context-removed="true"
            className="flex flex-wrap items-center gap-x-2 rounded-md border border-dashed border-[var(--border)] px-2 py-1"
          >
            <span className="min-w-0 break-words text-[var(--muted-foreground)] line-through">
              {entry.question}
            </span>
            <button
              type="button"
              onClick={() => onRestore(entry.entryId)}
              aria-label={t('context.restoreItem', { value: entry.question })}
              className="ml-auto inline-flex items-center gap-1 rounded p-0.5 text-[var(--muted-foreground)] hover:bg-[var(--accent-soft)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
              {t('context.restore')}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
