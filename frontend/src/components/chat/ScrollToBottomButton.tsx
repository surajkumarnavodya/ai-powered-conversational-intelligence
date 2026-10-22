import { ArrowDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

/** Floating "jump to the latest message" control -- shown only while
 * `Chat.tsx`'s own scroll container (via `useScrollToBottom`) is NOT
 * already at its bottom edge. Always mounted (never conditionally
 * rendered) so hiding it can fade/scale out instead of disappearing
 * instantly -- `visible` toggles opacity/scale/pointer-events/tabIndex
 * together, not the DOM presence.
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
}: {
  visible: boolean
  onClick: () => void
  bottomOffsetPx: number
}) {
  const { t } = useTranslation()
  const label = t('chat.scrollToLatest')

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-hidden={!visible}
      tabIndex={visible ? 0 : -1}
      style={{ bottom: bottomOffsetPx }}
      className={cn(
        'absolute left-1/2 z-20 flex h-10 w-10 -translate-x-1/2 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] shadow-md transition-[opacity,transform] duration-150 ease-out hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] motion-reduce:transition-none',
        visible ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-1 opacity-0',
      )}
    >
      <ArrowDown className="h-4 w-4" aria-hidden="true" />
    </button>
  )
}
