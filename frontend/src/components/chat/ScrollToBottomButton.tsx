import { ArrowDown } from 'lucide-react'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

/** Floating "jump to the latest message" control -- shown only while
 * `Chat.tsx`'s own scroll container (via `useScrollToBottom`) is NOT
 * already at its bottom edge, or while a question is processing (see
 * `processing` below). Always mounted (never conditionally rendered) so
 * hiding it can fade/scale out instead of disappearing instantly --
 * `visible` toggles opacity/scale/pointer-events/tabIndex together, not
 * the DOM presence.
 *
 * Positioned `absolute` within Chat.tsx's own full-height `relative`
 * wrapper (the same positioning context `QuestionNavigator` already
 * uses) rather than inside the scrolling region itself -- that's what
 * keeps it floating in a fixed spot above the messages instead of
 * scrolling away with them. `bottomOffsetPx` is the live-measured height
 * of the composer below it (plus a gap), so this never covers the input
 * box regardless of how tall the composer currently is (a grown
 * textarea, an attachment preview row, a narrow mobile viewport). */
export function ScrollToBottomButton({
  visible,
  onClick,
  bottomOffsetPx,
  processing = false,
  stageLabel,
}: {
  visible: boolean
  onClick: () => void
  bottomOffsetPx: number
  /** Whether a question in *this* conversation is still being answered.
   *
   * Purely an additional visual state: it draws the dotted progress ring
   * (`.dotted-ring` in index.css) and sets `aria-busy`, and changes
   * nothing about what the button does. Scrolling to the latest message
   * stays available throughout processing -- that is the whole point of
   * this control, and a question being in flight is exactly when a user
   * who scrolled up wants to get back down to watch it land.
   *
   * Driven by `chatStore.pendingQuestion`, which is non-null from a valid
   * submission until the request completes, fails, times out or is
   * aborted -- never a timer. See `Chat.tsx` for the derivation and for
   * what it deliberately does not cover. */
  processing?: boolean
  /** Already-localized name of the stage currently running, when the
   * server has told us one, e.g. "Schema" / "SQL" / "Execution". `null`
   * falls back to a plain "Processing…": the stage comes from
   * `/ask/stream` progress events, so when streaming is unavailable
   * there is genuinely nothing to name and nothing is invented. */
  stageLabel?: string | null
}) {
  const { t } = useTranslation()
  const label = t('chat.scrollToLatest')
  const statusId = useId()

  // The button's accessible *name* always describes its action, never its
  // state -- a control called "Processing…" would not tell a screen-reader
  // user what activating it does. The processing text is exposed as a
  // description instead, and `aria-busy` carries the state itself.
  //
  // Deliberately NOT an aria-live region: `ChatLiveRegion` already owns
  // announcing "the assistant is thinking" exactly once per question, and
  // specifically avoids re-announcing the per-second tick. A second live
  // region here would double every announcement.
  const status = processing
    ? stageLabel
      ? t('chat.processingStage', { stage: stageLabel })
      : t('chat.processing')
    : null

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={status ? `${label} — ${status}` : label}
      aria-hidden={!visible}
      aria-busy={processing || undefined}
      aria-describedby={status ? statusId : undefined}
      tabIndex={visible ? 0 : -1}
      style={{ bottom: bottomOffsetPx }}
      className={cn(
        // h-8/w-8 (32px) rather than the original 40px: the smaller disc
        // reads as a lighter-weight affordance and leaves more of the
        // conversation visible. `hit-area-44` keeps the *clickable* box at
        // a comfortable ~44px via a transparent pseudo-element, so the
        // visual shrink does not cost tap accuracy on a phone.
        'hit-area-44 absolute left-1/2 z-20 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] shadow-md transition-[opacity,transform] duration-150 ease-out hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] motion-reduce:transition-none',
        visible ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-1 opacity-0',
        // Ring is a pseudo-element at a negative inset, so adding it
        // cannot resize this button or shift anything around it.
        processing && 'dotted-ring',
      )}
    >
      {/* 14px inside the 32px disc -- the same icon-to-disc ratio the
          40px version had, so the arrow does not look crowded. */}
      <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
      {status && (
        <span id={statusId} className="sr-only">
          {status}
        </span>
      )}
    </button>
  )
}
