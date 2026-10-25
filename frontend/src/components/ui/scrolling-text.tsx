import { useLayoutEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

/** Pixels of hidden text revealed per second while actively scrolling --
 * tuned so the scroll reads as one constant, readable speed (what makes
 * this feel like Perplexity's own long-title hover treatment) rather than
 * a fixed-duration animation that would look sluggish for a short overflow
 * and unreadably fast for a long one. */
const SCROLL_SPEED_PX_PER_SECOND = 45
const MIN_LOOP_DURATION_MS = 1200
const MAX_LOOP_DURATION_MS = 24000

/** Trailing space after each copy of the label, so the end of one pass is
 * visibly separated from the start of the next instead of reading as one
 * run-on string ("...quarterly salesShow me quarterly sales").
 *
 * Deliberately small relative to a sidebar row: at ~40px it is a word
 * break passing through the box, never an empty box. A gap as wide as the
 * container would reintroduce exactly the blank stretch this component
 * exists to avoid. */
const LOOP_GAP_PX = 40

/** Single-line text that stays clipped at rest and, only once it actually
 * overflows its own box, scrolls continuously right-to-left for as long as
 * the pointer (or keyboard focus) stays on it -- Perplexity's own
 * treatment for a long history-list title, rather than this app's other
 * long-text pattern, `ExpandableText`'s clamp-and-"Show more", which is
 * for a multi-line paragraph, not a single compact label.
 *
 * The scroll **loops seamlessly and never blanks**. Two identical copies
 * of the label ride a single track, each with the same trailing gap, and
 * the track animates by exactly -50% -- one copy's width -- so the second
 * copy lands precisely where the first began and the restart is invisible.
 * This replaced a single-pass reveal that translated one copy by its own
 * overflow and stopped; that version could leave the row looking empty,
 * because the distance it travelled was whatever the last measurement
 * said, and a measurement taken while the panel was a different width
 * (the old code only re-measured on `text` changes and window `resize`,
 * not when the sidebar itself resized) could exceed the real overflow and
 * carry the text clean off the left edge. The loop is immune to that by
 * construction: there is always a copy of the label in the box, whatever
 * the measured distance turns out to be. A `ResizeObserver` on the
 * container now keeps the measurement honest as well, so the speed stays
 * right when the panel is resized.
 *
 * The full `text` is always in the DOM -- that's what makes the reveal
 * possible at all -- and only ever visually clipped; this component never
 * shortens `text` itself, the way a character-count helper such as
 * `lib/questionLabel.ts`'s `shortenQuestion` would. Clipping at the real
 * rendered width is the point: a character count cannot know how wide the
 * box actually is at this viewport. The second copy is `aria-hidden`, so
 * assistive tech reads the label once, and a native `title` tooltip
 * carries the full string for anyone who would rather not wait out a
 * scroll (including reduced-motion users, for whom the global
 * `prefers-reduced-motion` rule in `index.css` freezes the animation).
 *
 * At rest the inner text box is capped to the container's own width with
 * `text-overflow: ellipsis`, so a clipped label ends in a visible "..."
 * rather than a hard cut mid-character -- the reader can tell at a glance
 * that there is more to read (and therefore that hovering/focusing will
 * reveal it).
 *
 * A no-op (never scrolls, no duplicate copy rendered) when `text` already
 * fits -- measured via `scrollWidth` vs `clientWidth`, the exact
 * `ExpandableText`-established pattern in this codebase for "does this
 * actually overflow its box," just measuring width instead of height.
 * jsdom reports both as 0 by default, so this component's own tests mock
 * them explicitly; in that environment (or a real browser tab that's
 * merely too narrow to ever overflow) it degrades to "never scrolls,"
 * never to a crash or a stuck-mid-scroll state. */
export function ScrollingText({ text, className }: { text: string; className?: string }) {
  const containerRef = useRef<HTMLSpanElement>(null)
  const textRef = useRef<HTMLSpanElement>(null)
  const [textWidthPx, setTextWidthPx] = useState(0)
  const [containerWidthPx, setContainerWidthPx] = useState(0)
  const [isActive, setIsActive] = useState(false)

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container) return
    const measure = () => {
      // Only measurable at rest: while scrolling, the resting span is
      // replaced by the two-copy track, so there is no single-copy box
      // to measure. The width captured before activation is what the
      // animation needs, and a hover is not when the panel resizes.
      const textEl = textRef.current
      if (!textEl) return
      setTextWidthPx(textEl.scrollWidth)
      setContainerWidthPx(container.clientWidth)
    }
    measure()
    window.addEventListener('resize', measure)
    // A window `resize` is not the only thing that changes this box: the
    // sidebar collapsing/expanding, or a flyout opening beside it, resizes
    // the container with no window event at all. Observing the container
    // itself is what keeps a stale width from driving the animation -- the
    // same `ResizeObserver` technique `useScrollToBottom` already uses for
    // its own "the box changed under us" case.
    const resizeObserver = new ResizeObserver(measure)
    resizeObserver.observe(container)
    return () => {
      window.removeEventListener('resize', measure)
      resizeObserver.disconnect()
    }
  }, [text])

  const canScroll = textWidthPx > containerWidthPx
  // One full cycle covers a copy plus its gap, so the label keeps the same
  // reading speed whether it overflows by a word or by a sentence.
  const loopDurationMs = Math.round(
    Math.min(
      MAX_LOOP_DURATION_MS,
      Math.max(
        MIN_LOOP_DURATION_MS,
        ((textWidthPx + LOOP_GAP_PX) / SCROLL_SPEED_PX_PER_SECOND) * 1000,
      ),
    ),
  )
  const isScrolling = isActive && canScroll

  return (
    <span
      ref={containerRef}
      className={cn('inline-block min-w-0 overflow-hidden', className)}
      title={text}
      onMouseEnter={() => canScroll && setIsActive(true)}
      onMouseLeave={() => setIsActive(false)}
      onFocus={() => canScroll && setIsActive(true)}
      onBlur={() => setIsActive(false)}
    >
      {isScrolling ? (
        <span
          data-testid="marquee-track"
          className="marquee-track inline-flex w-max whitespace-nowrap"
          style={{ animationDuration: `${loopDurationMs}ms` }}
        >
          <span className="shrink-0" style={{ paddingRight: LOOP_GAP_PX }}>
            {text}
          </span>
          {/* The seam. Identical width to the copy above (same text, same
              trailing gap), which is what makes -50% land exactly one copy
              along and the restart invisible. Hidden from assistive tech
              so the label is still announced once. */}
          <span className="shrink-0" aria-hidden="true" style={{ paddingRight: LOOP_GAP_PX }}>
            {text}
          </span>
        </span>
      ) : (
        <span
          ref={textRef}
          className="inline-block whitespace-nowrap"
          style={{
            // `text-overflow` only takes effect on a box that is itself
            // both width-constrained and overflow-hidden -- the wrapper's
            // own `overflow-hidden` clips this span but cannot put an
            // ellipsis on it, which is why the cap lives here and not
            // there.
            maxWidth: '100%',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {text}
        </span>
      )}
    </span>
  )
}
