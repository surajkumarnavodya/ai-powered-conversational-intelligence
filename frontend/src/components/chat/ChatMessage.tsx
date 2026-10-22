import { Check, Pencil, User, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { ExpandableText } from '@/components/ui/expandable-text'
import { useLocalAuthStore } from '@/store/localAuthStore'

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
  actions,
}: {
  message: ChatMessageData
  /** Enables the edit control, and receives the edited question when the
   * person submits it. Only ever offered on a `user` bubble -- editing
   * what the assistant said would be a fabrication, not an edit. */
  onSubmitEdit?: (editedQuestion: string) => void
  /** Suppresses submission while another question is in flight, matching
   * the gate `ChatInput` and `RegenerateAnswerButton` already use. */
  editDisabled?: boolean
  /** Extra controls for the hover-revealed row under the bubble, rendered
   * before the edit button.
   *
   * A slot rather than named props, so this component stays presentational.
   * Copy needs nothing, but Share needs the conversation and a modal, and
   * pulling either in here would give a component that `SharedConversation`
   * renders a dependency on the chat store. The caller supplies the
   * buttons; this owns only where they appear and when. */
  actions?: ReactNode
}) {
  const { t } = useTranslation()
  const isUser = message.role === 'user'
  // Read from the live auth store rather than passed down: every caller
  // that renders a ChatMessage would otherwise have to thread the same
  // value through, and a replaced picture must update every already-
  // rendered turn at once, not only new ones.
  const avatarUrl = useLocalAuthStore((state) => state.user?.avatar_url ?? null)
  const canEdit = isUser && onSubmitEdit !== undefined
  const showActions = canEdit || actions !== undefined

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
    // `w-full` is load-bearing, not cosmetic. All three callers wrap this
    // in `<div className="flex justify-end">`, which makes this root a
    // shrink-to-fit flex item -- its width derived from its own content.
    // The bubble's `max-w-[75%]` below then resolved against *that*, so a
    // question was capped at 75% of its own natural text width and wrapped
    // early no matter how much of the column was free (a short question
    // broke onto a second line with most of the row empty beside it).
    // Filling the row makes the percentage resolve against the turn column,
    // which is what it was always meant to mean. `flex-row-reverse` still
    // packs the avatar and bubble against the right edge, so nothing moves
    // except how far a question may grow before it wraps.
    <div className={`group flex w-full gap-2.5 ${isUser ? 'flex-row-reverse' : ''}`}>
      <span className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
        {isUser ? (
          // The signed-in user's own profile picture when they have set
          // one, otherwise the default icon unchanged. `object-cover`
          // rather than `object-contain` because the stored image is
          // already a square the server centre-cropped -- cover fills the
          // circle, contain would letterbox it inside one.
          //
          // `alt=""` on purpose: the avatar is decorative here. Which turn
          // is the user's is already carried by the bubble's own side and
          // styling, so announcing "your profile picture" on every message
          // would be noise to a screen reader, not information.
          avatarUrl ? (
            <img src={avatarUrl} alt="" className="h-full w-full object-cover" />
          ) : (
            <User className="h-4 w-4" />
          )
        ) : (
          <img src="/favicon.png" alt="" className="h-full w-full object-contain" />
        )}
      </span>
      {/* The bubble and its action row share one column, so the actions
          sit under the bubble and align to its edge -- the arrangement
          the reference screenshots use. The width cap moved here from the
          bubble now that there are two things to constrain.

          Wider share of the line on a phone, where 75% of a 360px column
          leaves a question wrapping every four or five words; back to 75%
          from `sm` up, where the reading column is wide enough for the
          asymmetry to read as alignment rather than waste.

          `min-w-0` lets this shrink below its content when the row runs
          out of room. It changes nothing at normal widths -- a flex item
          only shrinks once there is overflow -- but on a very narrow
          phone the 32px avatar plus an 85% bubble can exceed the row, and
          without it the bubble would push past the edge instead of
          wrapping. */}
      <div
        className={`flex min-w-0 flex-col gap-1 ${
          isEditing
            ? // Editing takes the whole turn column, not the 75% a *read*
              // bubble is capped at. A question is read as a quote, so
              // keeping it narrow and ragged-right is right at rest; it is
              // edited as a text field, where a box sized to "Top selling
              // products." gives a few characters of room to rewrite in and
              // leaves most of the row empty beside it. ChatGPT and
              // Perplexity both widen on edit for this reason. `flex-1`
              // rather than `w-full` so it claims the space left over
              // beside the avatar (which is `shrink-0`) instead of the
              // whole row, and `items-stretch` so the bubble actually
              // fills what the column claimed. Percentages are gone
              // entirely here, so this is width-independent: the same rule
              // holds on a phone and on a wide desktop.
              'flex-1 items-stretch'
            : `max-w-[85%] sm:max-w-[75%] ${isUser ? 'items-end' : 'items-start'}`
        }`}
      >
        <div
          // `w-full` only while editing, so the textarea gets the whole
          // column instead of the handful of characters the question
          // happened to occupy.
          className={`min-w-0 rounded-2xl px-3.5 py-2 text-sm ${
            isEditing ? 'w-full' : 'max-w-full'
          } ${
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
            /* Long pasted questions collapse to a few lines with a "Show
               more" toggle (ChatGPT/Perplexity's treatment) instead of
               pushing the rest of the conversation down by default. */
            <ExpandableText
              text={message.content}
              maxLines={5}
              fadeToColor={isUser ? 'var(--accent)' : 'var(--card)'}
            />
          )}
        </div>

        {/* Hidden until the turn is hovered, or until something in the row
            takes focus: a clean bubble at rest with its actions underneath
            on approach, which is the arrangement the reference uses.

            `opacity-0` rather than `hidden`, so the row keeps its height
            and the conversation does not jump as the pointer moves down
            it. That would leave buttons invisible but still hit-testable,
            which `pointer-events-none` closes -- the group turns them back
            on at the same moment it fades them in, and the row sits inside
            the group, so moving the pointer from the bubble down onto a
            button keeps them revealed.

            `focus-within` is the keyboard half. Pointer events never
            affect Tab or Enter, so the row stays reachable without a
            mouse, and this is what makes it visible when it is. */}
        {showActions && !isEditing && (
          <div className="pointer-events-none flex items-center gap-0.5 opacity-0 transition-opacity duration-[var(--duration-base)] focus-within:pointer-events-auto focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100">
            {actions}
            {canEdit && (
              <Button
                size="icon"
                variant="ghost"
                onClick={startEditing}
                aria-label={t('chat.editQuestion')}
                title={t('chat.editQuestion')}
                className="h-7 w-7 sm:h-7 sm:w-7"
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
