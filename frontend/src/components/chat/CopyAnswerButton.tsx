import { Check, Copy } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ActionTooltip } from '@/components/ui/action-tooltip'
import { Button } from '@/components/ui/button'
import { copyMarkdownToClipboard } from '@/lib/clipboard'
import { cn } from '@/lib/utils'

/** Copies one assistant answer, icon-only, with the label on hover and on
 * keyboard focus.
 *
 * `answer` is always `lib/history.buildAnswerMarkdown(entry)` -- the
 * canonical Markdown for that one turn, assembled from the stored response
 * state. Deliberately **not** scraped from the rendered card: the DOM also
 * holds button labels, timing badges, "Thinking…" placeholders and the
 * question above it, none of which belong in a pasted answer. Source links
 * are part of that Markdown and are kept; the question is not, and is not.
 *
 * The copied-confirmation has to be *visible*, not just audible -- losing
 * the text label must not cost the "it worked" feedback. So the check icon
 * swaps in for 1.5s, and the label the tooltip and the accessible name
 * both report changes with it, which means a screen reader hears the
 * confirmation rather than only sighted users seeing a glyph change.
 */
export function CopyAnswerButton({
  answer,
  disabled = false,
  className,
}: {
  answer: string
  /** True while the turn is still a loading placeholder or mid-stream.
   * Copying a half-written answer silently produces a truncated paste,
   * which is worse than not offering it yet. */
  disabled?: boolean
  className?: string
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  // Held so the 1.5s reset can be cancelled. Without this, unmounting the
  // turn (a conversation switch, a regenerate) leaves a timer that fires
  // setState on a gone component; and a second copy inside the window used
  // to let the first timer cut the second confirmation short.
  const resetTimer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current)
    },
    [],
  )

  const handleCopy = async () => {
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current)
    try {
      await copyMarkdownToClipboard(answer)
    } catch {
      // Only reachable when *both* the rich write and the plain-text
      // fallback failed -- an insecure context, a denied permission, or a
      // browser without the API. Reported inline rather than through the
      // app's toast: a leaf button should not need app-level context to
      // work (`SharedConversation` renders answers too), and a message
      // beside the control that failed is less disruptive than one that
      // appears in a corner of the screen. Never surface the answer text
      // itself here, and never log it.
      setCopied(false)
      setFailed(true)
      resetTimer.current = window.setTimeout(() => {
        setFailed(false)
        resetTimer.current = null
      }, 6000)
      return
    }
    // Only after the write actually resolved: showing "Copied" on a failed
    // write is worse than showing nothing, because the person walks away
    // believing they have it.
    setFailed(false)
    setCopied(true)
    resetTimer.current = window.setTimeout(() => {
      setCopied(false)
      resetTimer.current = null
    }, 1500)
  }

  const label = copied ? t('common.copied') : t('common.copyAnswer')

  return (
    <>
      {/* Polite, and empty at rest, so nothing is announced on mount or on
          a re-render -- one update per copy attempt is the whole traffic.
          Polite for the failure too: it is recoverable and the fallback is
          stated, so interrupting with `assertive` would be louder than the
          situation warrants. */}
      <span role="status" aria-live="polite" className="sr-only">
        {copied ? t('common.copied') : failed ? `${t('common.copyFailed')}. ${t('common.copyFailedHint')}` : ''}
      </span>
      <ActionTooltip label={label}>
        <Button
          // Explicit: this renders inside the chat column, and a button
          // that defaults to `submit` would post the composer form.
          type="button"
          size="icon"
          variant="ghost"
          disabled={disabled}
          aria-label={label}
          onClick={() => void handleCopy()}
          className={cn(
            'text-[var(--chat-action-icon)] hover:bg-[var(--chat-action-hover-bg)] focus-visible:ring-[var(--chat-action-focus-ring)]',
            className,
          )}
        >
          {copied ? (
            <Check className="h-4 w-4 text-[var(--success)]" />
          ) : (
            <Copy className="h-4 w-4" />
          )}
        </Button>
      </ActionTooltip>
      {/* Visible counterpart to the status line above. `aria-hidden` so the
          live region is the only thing that speaks it -- otherwise a screen
          reader hears the same sentence twice. Clears itself after 6s, long
          enough to read and act on. */}
      {failed && (
        <span
          aria-hidden="true"
          className="text-xs text-[var(--warning)]"
          data-testid="copy-answer-failed"
        >
          {t('common.copyFailedHint')}
        </span>
      )}
    </>
  )
}
