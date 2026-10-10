import { useLayoutEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

/** Pixels of hidden text revealed per second while actively scrolling --
 * tuned so the scroll reads as one constant, readable speed (what makes
 * this feel like Perplexity's own long-title hover treatment) rather than
 * a fixed-duration animation that would look sluggish for a short overflow
 * and unreadably fast for a long one. */
const SCROLL_SPEED_PX_PER_SECOND = 45
const MIN_SCROLL_DURATION_MS = 400
const MAX_SCROLL_DURATION_MS = 6000
const RESET_DURATION_MS = 200

/** Single-line text that stays clipped at rest and, only once it actually
 * overflows its own box, scrolls leftward on hover/focus to reveal the
 * rest -- then snaps back the moment the pointer/focus leaves (Perplexity's
 * own treatment for a long history-list title, rather than this app's
 * other long-text pattern, `ExpandableText`'s clamp-and-"Show more", which
 * is for a multi-line paragraph, not a single compact label).
 *
 * The full `text` is always in the DOM -- that's what makes the reveal
 * possible at all -- and only ever visually clipped (see the ellipsis note
 * below); this component never shortens `text` itself, the way a
 * character-count helper such as `lib/questionLabel.ts`'s `shortenQuestion`
 * would. Clipping at the real rendered width is the point: a character
 * count cannot know how wide the box actually is at this viewport.
 *
 * At rest the inner text box is additionally capped to the container's
 * own width with `text-overflow: ellipsis`, so a clipped label ends in a
 * visible "..." rather than a hard cut mid-character -- the reader can
 * tell at a glance that there is more to read (and therefore that
 * hovering/focusing will reveal it). Those three resting-only properties
 * are dropped while actively scrolling: an inner box still capped to the
 * container's width would simply translate its own ellipsis sideways and
 * never reveal anything.
 *
 * A no-op (no transform, ever) when `text` already fits -- measured via
 * `scrollWidth` vs `clientWidth` on mount and on window resize, the exact
 * `ExpandableText`-established pattern in this codebase for "does this
 * actually overflow its box," just measuring width instead of height.
 * jsdom reports both as 0 by default, so this component's own tests mock
 * them explicitly; in that environment (or a real browser tab that's
 * merely too narrow to ever overflow) it degrades to "never scrolls,"
 * never to a crash or a stuck-mid-scroll state. */
export function ScrollingText({ text, className }: { text: string; className?: string }) {
  const containerRef = useRef<HTMLSpanElement>(null)
  const textRef = useRef<HTMLSpanElement>(null)
  const [overflowPx, setOverflowPx] = useState(0)
  const [isActive, setIsActive] = useState(false)

  useLayoutEffect(() => {
    const container = containerRef.current
    const textEl = textRef.current
    if (!container || !textEl) return
    const measure = () => {
      const diff = textEl.scrollWidth - container.clientWidth
      setOverflowPx(diff > 0 ? diff : 0)
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [text])

  const canScroll = overflowPx > 0
  const scrollDurationMs = Math.round(
    Math.min(MAX_SCROLL_DURATION_MS, Math.max(MIN_SCROLL_DURATION_MS, (overflowPx / SCROLL_SPEED_PX_PER_SECOND) * 1000)),
  )

  return (
    <span
      ref={containerRef}
      className={cn('inline-block min-w-0 overflow-hidden', className)}
      onMouseEnter={() => canScroll && setIsActive(true)}
      onMouseLeave={() => setIsActive(false)}
      onFocus={() => canScroll && setIsActive(true)}
      onBlur={() => setIsActive(false)}
    >
      <span
        ref={textRef}
        className="inline-block whitespace-nowrap"
        style={{
          transform: isActive ? `translateX(-${overflowPx}px)` : 'translateX(0px)',
          transition: isActive
            ? `transform ${scrollDurationMs}ms linear`
            : `transform ${RESET_DURATION_MS}ms ease-out`,
          // `text-overflow` only takes effect on a box that is itself both
          // width-constrained and overflow-hidden -- the wrapper's own
          // `overflow-hidden` clips this span but cannot put an ellipsis
          // on it, which is why the cap lives here and not there.
          maxWidth: isActive ? 'none' : '100%',
          overflow: isActive ? 'visible' : 'hidden',
          textOverflow: isActive ? 'clip' : 'ellipsis',
        }}
      >
        {text}
      </span>
    </span>
  )
}
