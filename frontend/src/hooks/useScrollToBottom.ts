import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

/** Treated as "at the bottom" once within this many pixels of it -- small
 * enough to never show the button for a trivial, probably-fractional
 * remainder (sub-pixel scroll math, a rounded border), large enough that a
 * real browser's own scroll-math jitter never flips it back and forth. */
const DEFAULT_THRESHOLD_PX = 20

export interface UseScrollToBottomResult {
  /** Whether `containerRef`'s own scrollable element is currently at (or
   * within the threshold of) its bottom edge -- the single source of
   * truth for both the floating "Jump to Bottom" button's visibility and
   * Chat.tsx's own pre-existing auto-follow-the-newest-turn behavior. */
  isAtBottom: boolean
  /** A ref mirror of `isAtBottom`, updated synchronously (never lagging a
   * render) -- read by Chat.tsx's own "keep the newest turn in view"
   * effect so it can check the *latest* scroll position without putting
   * `isAtBottom` in that effect's own dependency list (which would make
   * it re-run on every scroll, not just when a new turn actually
   * arrives). The same ref-mirror-of-reactive-state technique
   * `useActiveElementId` already uses for its own "read the latest value
   * without over-firing the effect" need. Safe to also *write* from the
   * outside (e.g. forcing it back to `true` right before an unconditional
   * jump, such as switching conversations) -- it's a plain mutable ref. */
  isAtBottomRef: RefObject<boolean>
  /** Scrolls `containerRef`'s element to its latest content. Defaults to
   * smooth, unless the user prefers reduced motion (matches the
   * pre-existing behavior this replaces) -- pass `'auto'` explicitly for
   * an instant jump (e.g. a fresh conversation load). */
  scrollToBottom: (behavior?: ScrollBehavior) => void
}

/** Tracks whether a scrollable container is at its own bottom edge, and
 * exposes the one `scrollToBottom` primitive both Chat.tsx's auto-follow
 * effect and the floating `ScrollToBottomButton` call -- one scroll-state
 * system, not two independent ones.
 *
 * rAF-throttled: a fast flick/trackpad fling fires many native `scroll`
 * events per second, but position only needs recomputing once per frame
 * to stay visually correct -- doing it on every single event would be
 * wasted work and risks the exact "flickering" a naive implementation
 * would show.
 *
 * Also re-measures via a `ResizeObserver` on the container itself: a
 * turn's own content can grow `scrollHeight` with no `scroll` event at
 * all (an image finishing its load, a chart rendering in below the
 * fold), and that alone can turn "at the bottom" into "not anymore"
 * without the user doing anything. */
export function useScrollToBottom(
  containerRef: RefObject<HTMLElement | null>,
  thresholdPx: number = DEFAULT_THRESHOLD_PX,
): UseScrollToBottomResult {
  const [isAtBottom, setIsAtBottom] = useState(true)
  const isAtBottomRef = useRef(true)
  const rafRef = useRef<number | null>(null)
  const rafPendingRef = useRef(false)

  const scrollToBottom = useCallback(
    (behavior?: ScrollBehavior) => {
      const el = containerRef.current
      if (!el) return
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      el.scrollTo({ top: el.scrollHeight, behavior: behavior ?? (reduceMotion ? 'auto' : 'smooth') })
    },
    [containerRef],
  )

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const measure = () => {
      rafPendingRef.current = false
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= thresholdPx
      isAtBottomRef.current = atBottom
      setIsAtBottom((current) => (current === atBottom ? current : atBottom))
    }
    const onScroll = () => {
      if (rafPendingRef.current) return
      // Set *before* scheduling, not derived from requestAnimationFrame's
      // own return value -- a synchronous test double for rAF (see this
      // hook's own test file) would otherwise invoke `measure` (which
      // clears this) before the scheduling call below even returns,
      // leaving the gate incorrectly stuck "pending" forever after the
      // very first scroll event.
      rafPendingRef.current = true
      rafRef.current = requestAnimationFrame(measure)
    }

    measure()
    el.addEventListener('scroll', onScroll, { passive: true })
    const resizeObserver = new ResizeObserver(measure)
    resizeObserver.observe(el)

    return () => {
      el.removeEventListener('scroll', onScroll)
      resizeObserver.disconnect()
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [containerRef, thresholdPx])

  return { isAtBottom, isAtBottomRef, scrollToBottom }
}
