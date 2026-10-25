import { Loader2, MessagesSquare, Minus, Plus } from 'lucide-react'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { groupConversationsByRecency, type ConversationGroupKey, type ConversationSummary } from '@/lib/history'
import { ConversationListItem } from './ConversationListItem'

const GROUP_LABEL_KEYS: Record<ConversationGroupKey, string> = {
  today: 'history.today',
  yesterday: 'history.yesterday',
  previous7Days: 'history.previous7Days',
  older: 'history.older',
}

export interface ConversationListProps {
  conversations: ConversationSummary[]
  activeConversationId: string | null
  isLoadingConversation: boolean
  isLoading: boolean
  renamingId: string | null
  renameValue: string
  onRenameValueChange: (value: string) => void
  onSelect: (id: string) => void
  onStartRename: (id: string, currentTitle: string) => void
  onCommitRename: (id: string) => void
  onCancelRename: () => void
  onDelete: (id: string, title: string) => void
  onShare: (id: string, title: string) => void
}

/** Recency-grouped conversation list -- extracted from the old
 * HistoryDrawer. Owns only rendering/grouping; every mutation (select,
 * rename, delete) is a callback into the caller, which is what makes this
 * reusable from both the desktop Sidebar and the mobile drawer without
 * duplicating any chatStore wiring. */
export function ConversationList({
  conversations,
  activeConversationId,
  isLoadingConversation,
  isLoading,
  renamingId,
  renameValue,
  onRenameValueChange,
  onSelect,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onDelete,
  onShare,
}: ConversationListProps) {
  const { t } = useTranslation()
  const groups = groupConversationsByRecency(conversations)
  // Collapsed groups, by key. Held here rather than in `chatStore`
  // because it is view state for this one rendering of the list, not
  // part of the conversation model -- the mobile drawer and the desktop
  // sidebar each get their own, which is what you want when one is open
  // over the other. It is deliberately not persisted: a group that
  // silently stayed collapsed across a reload would look like missing
  // history.
  const [collapsedKeys, setCollapsedKeys] = useState<ReadonlySet<ConversationGroupKey>>(
    () => new Set(),
  )
  // One stable id root per mounted list, so two lists on the page never
  // produce colliding `aria-controls` targets.
  const groupIdRoot = useId()

  const toggleGroup = (key: ConversationGroupKey) => {
    setCollapsedKeys((current) => {
      const next = new Set(current)
      if (!next.delete(key)) next.add(key)
      return next
    })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" aria-hidden="true" />
      </div>
    )
  }

  if (conversations.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
        <MessagesSquare className="h-8 w-8 text-[var(--muted-foreground)]" aria-hidden="true" />
        <p className="text-sm text-[var(--muted-foreground)]">{t('history.empty')}</p>
      </div>
    )
  }

  return (
    <>
      {groups.map((group) => {
        const label = t(GROUP_LABEL_KEYS[group.key])
        const isCollapsed = collapsedKeys.has(group.key)
        const itemsId = `${groupIdRoot}-${group.key}`
        const ToggleIcon = isCollapsed ? Plus : Minus
        return (
          // `mb-0.5` keeps the groups close now that each heading is a
          // real 24px control with its own padding -- the vertical budget
          // moved from dead margin into a hit target.
          <div key={group.key} className="mb-0.5">
            {/* The heading is the toggle: one control rather than a label
                plus a separate +/- button beside it, so the whole row is
                the target and there is no second thing to aim at. A
                nested button would also be invalid HTML.

                `font-bold` and `--chat-text-primary` (not
                `--muted-foreground`) are the requested black, bold
                treatment. The token rather than a literal `#000` because
                it is redefined per theme -- literal black would be
                unreadable on the dark surface. */}
            <button
              type="button"
              onClick={() => toggleGroup(group.key)}
              aria-expanded={!isCollapsed}
              aria-controls={itemsId}
              // Names the action as well as the group. It still contains
              // the visible label verbatim, which is what WCAG 2.5.3
              // (Label in Name) requires of a control whose accessible
              // name overrides its own text.
              aria-label={t(isCollapsed ? 'history.expandGroup' : 'history.collapseGroup', {
                group: label,
              })}
              className="flex w-full items-center gap-1 rounded-md px-1 py-1 text-left text-[11px] font-bold uppercase tracking-wide text-[var(--chat-text-primary)] transition-colors hover:bg-[var(--chat-hover-bg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
            >
              <ToggleIcon className="h-3 w-3 shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">{label}</span>
            </button>
            {/* The `hidden` attribute, not conditional rendering: it
                resolves to `display:none`, which already takes the whole
                subtree out of the tab order and the accessibility tree,
                and it keeps the element present so `aria-controls` above
                always resolves to a real node. */}
            <div id={itemsId} hidden={isCollapsed} className="flex flex-col gap-px">
              {group.items.map((conversation) => (
                <ConversationListItem
                  key={conversation.id}
                  conversation={conversation}
                  isActive={conversation.id === activeConversationId}
                  isLoading={isLoadingConversation}
                  isRenaming={renamingId === conversation.id}
                  renameValue={renameValue}
                  onRenameValueChange={onRenameValueChange}
                  onSelect={() => onSelect(conversation.id)}
                  onStartRename={() => onStartRename(conversation.id, conversation.title)}
                  onCommitRename={() => onCommitRename(conversation.id)}
                  onCancelRename={onCancelRename}
                  onDelete={() => onDelete(conversation.id, conversation.title)}
                  onShare={() => onShare(conversation.id, conversation.title)}
                />
              ))}
            </div>
          </div>
        )
      })}
    </>
  )
}
