import { Check, Pencil, User, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ExpandableText } from '@/components/ui/expandable-text'

export interface ChatMessageData {
  role: 'user' | 'assistant'
  content: string
}

/** One chat bubble.
 *
 * Shared by three callers with deliberately different capabilities:
 * `TurnCard` (the live conversation), `PendingTurn` (a question still in
 * flight) and `SharedConversation` (a public read-only view). That last
 * one is why editing is **opt-in** rather than on by default: a share can
 * be opened by someone holding no roles at all, and a control that leaked
 * into it would invite an action that caller must not have. A caller that
 * passes no `onSubmitEdit` gets exactly the bubble it got before.
 */
export function ChatMessage({
  message,
  onSubmitEdit,
  editDisabled = false,
}: {
  message: ChatMessageData
  /** Enables the edit control, and receives the edited question when the
   * person submits it. Only ever offered on a `user` bubble -- editing
   * what the assistant said would be a fabrication, not an edit. */
  onSubmitEdit?: (editedQuestion: string) => void
  /** Suppresses submission while another question is in flight, matching
   * the gate `ChatInput` and `RegenerateAnswerButton` already use. */
  editDisabled?: boolean
}) {
  const { t } = useTranslation()
  const isUser = message.role === 'user'
  const canEdit = isUser && onSubmitEdit !== undefined

  const [isEditing, setIsEditing] = useState(false)
  const [draft, setDraft] = useState(message.content)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Entering edit mode starts from what is on screen now, not from
  // whatever the draft held when it was last abandoned.
  const startEditing = () => {
    setDraft(message.content)
    setIsEditing(true)
  }

  useEffect(() => {
    if (!isEditing) return
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.focus()
    // Caret at the end rather than selecting everything: the common edit
    // is a tweak to a long question, and a select-all means one stray
    // keystroke destroys it.
    textarea.setSelectionRange(textarea.value.length, textarea.value.length)
  }, [isEditing])

  const submit = () => {
    const edited = draft.trim()
    if (!edited || editDisabled) return
    setIsEditing(false)
    onSubmitEdit?.(edited)
  }

  const cancel = () => {
    setIsEditing(false)
    setDraft(message.content)
  }

  return (
    <div className={`group flex gap-2.5 ${isUser ? 'flex-row-reverse' : ''}`}>
      <span className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
        {isUser ? <User className="h-4 w-4" /> : <img src="/favicon.png" alt="" className="h-full w-full object-contain" />}
      </span>
      <div
        // Wider share of the line on a phone, where 75% of a 360px
        // column leaves a question wrapping every four or five words;
        // back to 75% from `sm` up, where the reading column is wide
        // enough for the asymmetry to read as alignment rather than
        // waste.
        className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm sm:max-w-[75%] ${
          isUser
            ? 'bg-[var(--accent)] text-[var(--accent-foreground)]'
            : 'border border-[var(--border)] bg-[var(--card)]'
        }`}
      >
        {isEditing ? (
          <div className="flex flex-col gap-2">
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter submits and Shift+Enter inserts a newline -- the
                // same contract the composer already uses, so the two
                // places a question is typed behave identically.
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  submit()
                } else if (event.key === 'Escape') {
                  event.preventDefault()
                  cancel()
                }
              }}
              rows={Math.min(8, draft.split('\n').length + 1)}
              aria-label={t('chat.editQuestion')}
              className="w-full resize-y rounded-lg bg-[var(--card)] px-2 py-1.5 text-sm text-[var(--foreground)] outline-none ring-1 ring-[var(--border)] focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
            />
            <div className="flex justify-end gap-1.5">
              <button
                type="button"
                onClick={cancel}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs hover:bg-black/10"
              >
                <X className="h-3.5 w-3.5" />
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={editDisabled || draft.trim().length === 0}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs hover:bg-black/10 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Check className="h-3.5 w-3.5" />
                {t('chat.submitEdit')}
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Long pasted questions collapse to a few lines with a "Show
                more" toggle (ChatGPT/Perplexity's treatment) instead of
                pushing the rest of the conversation down by default. */}
            <ExpandableText
              text={message.content}
              maxLines={5}
              fadeToColor={isUser ? 'var(--accent)' : 'var(--card)'}
            />
            {canEdit && (
              <div className="mt-1 flex justify-end">
                <button
                  type="button"
                  onClick={startEditing}
                  aria-label={t('chat.editQuestion')}
                  title={t('chat.editQuestion')}
                  // Visible on hover and whenever it holds focus, so it is
                  // reachable by keyboard rather than mouse-only.
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs opacity-0 transition-opacity hover:bg-black/10 focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <Pencil className="h-3 w-3" />
                  {t('chat.editQuestion')}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
