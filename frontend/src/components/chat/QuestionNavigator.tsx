import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent as ReactPointerEvent, RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { ScrollingText } from '@/components/ui/scrolling-text'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useActiveElementId } from '@/hooks/useActiveElementId'
import type { QueryHistoryEntry } from '@/lib/history'
import { collapseQuestionWhitespace } from '@/lib/questionLabel'
import { cn } from '@/lib/utils'

/** Compact "jump to question" control for the current conversation --
 * renders only once there are at least two navigable questions (one
 * question has nothing to navigate *between*, so showing the control at
 * all would just be visual noise -- see the component's own test for
 * this exact "one question" case).
 *
 * Collapsed by default to a small hamburger/sandwich icon, vertically
 * centered on the right edge of the chat column (`top-1/2
 * -translate-y-1/2`, relative to Chat.tsx's full-height `relative`
 * wrapper) -- it never pushes or resizes the answer area, and in its
 * default state it occupies only a narrow column's width, not a
 * fixed-width panel. Clicking the icon expands a flyout panel with the
 * full question list, positioned to the *left* of the icon as an
 * absolutely-positioned overlay (so expanding it still never shifts or
 * resizes the chat/answer area underneath). The flyout is only ever
 * mounted in the DOM while open (not merely visually hidden), which is
 * what keeps it genuinely absent from the accessibility tree (and from
 * this component's own tests) until it's actually open.
 *
 * **The panel opens on hover and closes when the pointer leaves**, with no
 * close button. Hover-to-open was tried once before and reverted, so the
 * two things that made it unusable then are both fixed here rather than
 * re-litigated:
 *
 * 1. *The dead gap.* The old panel sat to the **left** of the icon with an
 *    8px margin (`mr-2`) between them. That gap belonged to neither the
 *    wrapper nor any descendant, so a pointer travelling from icon to list
 *    crossed it, fired `mouseleave`, and closed the panel before it could
 *    be reached. The panel is now anchored `right-0` -- flush to the
 *    wrapper's own right edge, *underneath* the icon rather than beside it
 *    -- so icon and list are one continuous hover surface with no gap to
 *    cross at all. The icon's lane is reserved by the panel's `pr-9`, so
 *    nothing is ever hidden behind it.
 * 2. *Twitchy closes.* Leaving does not close immediately; it arms a
 *    `CLOSE_DELAY_MS` timer that any re-entry cancels. A pointer that
 *    clips a corner, or crosses a sub-pixel seam between two children,
 *    re-enters long before the timer fires. This is the ordinary menu
 *    grace period, and it is what makes the interaction feel settled
 *    rather than flickery.
 *
 * Hover is a mouse affordance only: `onPointerEnter`/`onPointerLeave`
 * ignore `pointerType: 'touch'`/`'pen'`, because a touch tap synthesizes a
 * mouseenter *before* the click and the two would fight -- hover would
 * open the panel and the click would immediately toggle it shut. Touch
 * users get the same control through the toggle (tap to open, tap to
 * close) plus tap-outside-to-dismiss. Keyboard users get the toggle and
 * Escape. Closing on `blur` is still deliberately absent: it would shut
 * the panel the moment a keyboard user tabbed into the list.
 *
 * **The panel must stay inside the chat column's own box.** `AppShell`'s
 * `<main>` is `overflow-hidden`, so anything that reached past it would be
 * clipped rather than overlaid. Anchoring to `right-0` of a wrapper that
 * is itself inset by `right-4` keeps the panel 16px clear of that edge,
 * and the `max-w-[calc(100vw-2rem)]` clamp keeps the left edge on screen
 * at phone widths. A future change that moves the panel outward (a
 * negative offset, a wider fixed width) will be silently cropped there.
 *
 * The icon sits inside a tall vertical rail (`h-44`, centered via flex)
 * whose hairline and end-cap dots (purely decorative, `aria-hidden`)
 * mark where the control lives against an otherwise empty right edge.
 * The rail no longer needs to be an enlarged *hit* target now that
 * nothing depends on the pointer staying within it, so the wrapper is
 * `pointer-events-none` and only the real controls (the toggle, and the
 * panel itself) opt back in -- otherwise this 36x176px column would sit
 * over the conversation swallowing clicks for no reason.
 *
 * Reuses `entry.entryId` (already the conversation's own stable identity
 * -- `TurnCard` renders each turn at DOM id `turn-${entryId}`) as the one
 * and only way an item is matched to its message: never question text,
 * never array position. Two entries with byte-identical question text
 * still get two independent entryIds and therefore navigate to two
 * independent elements.
 *
 * Each item's label is the *full* question (whitespace-collapsed, never
 * pre-shortened), rendered through `ScrollingText` -- clipped to one line
 * with a trailing ellipsis at rest, scrolling left on hover/focus to
 * reveal the rest once it's actually long enough to overflow
 * (Perplexity's own treatment for a long history-list title). The
 * accessible tooltip below is independent of this and unchanged: a screen
 * reader or a sighted keyboard user who never triggers the hover
 * animation still gets the complete question through it.
 *
 * Each item also carries an explicit `aria-label` ("Navigate to question:
 * ...") rather than letting its accessible name fall out of its own
 * rendered text. Two reasons: the rendered text begins with the item's
 * decorative position number, which a screen reader would otherwise
 * announce as a bare, contextless "1"; and the name should say what
 * activating the item *does*, not merely restate the question. The
 * position number is therefore `aria-hidden` -- it orients a sighted
 * reader scanning the list and carries nothing for anyone else.
 */
/** How long the pointer may be outside the control before it collapses.
 * Long enough to absorb a clipped corner or a sub-pixel seam between the
 * icon and the list; short enough that a deliberate "move away" still
 * feels like it closed on purpose. */
const CLOSE_DELAY_MS = 180

/** Shared look for the decorative rail pieces, including the fade they do
 * when the panel takes over their lane. */
const railSegment =
  'bg-[var(--border)] transition-opacity duration-150 ease-out motion-reduce:transition-none'

export function QuestionNavigator({
  entries,
  scrollContainerRef,
}: {
  entries: QueryHistoryEntry[]
  scrollContainerRef: RefObject<HTMLDivElement | null>
}) {
  const { t } = useTranslation()
  // A question that never actually resolved to real text (an empty/invalid
  // reconstructed entry) has nothing meaningful to show or jump to.
  //
  // Memoized on `entries` alone: this component re-renders on *any*
  // `queryHistory` change -- a turn's SQL being edited, a chart option
  // being tweaked -- and in a 100+ question conversation re-deriving both
  // lists each time is pure waste. It also keeps `domIds` referentially
  // stable, which is what lets `useActiveElementId` skip re-subscribing
  // its observer on those unrelated renders.
  const navigable = useMemo(
    () => entries.filter((entry) => entry.question.trim() !== ''),
    [entries],
  )
  const domIds = useMemo(() => navigable.map((entry) => `turn-${entry.entryId}`), [navigable])
  const activeDomId = useActiveElementId(domIds, scrollContainerRef)

  const [isOpen, setIsOpen] = useState(false)
  // Focus returns here whenever the panel is dismissed with Escape, so
  // dismissing never strands focus on a node that has just been unmounted.
  const toggleRef = useRef<HTMLButtonElement>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const cancelPendingClose = useCallback(() => {
    if (closeTimerRef.current === null) return
    clearTimeout(closeTimerRef.current)
    closeTimerRef.current = null
  }, [])

  const openNow = useCallback(() => {
    cancelPendingClose()
    setIsOpen(true)
  }, [cancelPendingClose])

  /** Arms the grace period rather than closing outright -- see the
   * component docstring's "twitchy closes". Any re-entry cancels it. */
  const closeAfterGrace = useCallback(() => {
    cancelPendingClose()
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null
      setIsOpen(false)
    }, CLOSE_DELAY_MS)
  }, [cancelPendingClose])

  // A pending timer that fires after unmount would call setState on a dead
  // component; one that survives a close/reopen would shut the panel the
  // user just reopened.
  useEffect(() => cancelPendingClose, [cancelPendingClose])

  // Tap-outside-to-dismiss -- the touch counterpart to "the pointer left".
  // `pointerdown` rather than `click` so a tap that starts outside dismisses
  // immediately, and in the capture phase so it still runs when the target
  // stops propagation. Bound only while open, so the app carries no global
  // listener in its normal state.
  useEffect(() => {
    if (!isOpen) return
    const handlePointerDownOutside = (event: PointerEvent) => {
      const wrapper = wrapperRef.current
      if (!wrapper) return
      if (event.target instanceof Node && wrapper.contains(event.target)) return
      cancelPendingClose()
      setIsOpen(false)
    }
    document.addEventListener('pointerdown', handlePointerDownOutside, true)
    return () => document.removeEventListener('pointerdown', handlePointerDownOutside, true)
  }, [isOpen, cancelPendingClose])

  // Hover is a mouse-only affordance (see the docstring): a touch tap
  // synthesizes mouseenter before its click, so honouring it here would
  // open the panel and let the click immediately toggle it shut again.
  const handlePointerEnter = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === 'touch' || event.pointerType === 'pen') return
    openNow()
  }

  const handlePointerLeave = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === 'touch' || event.pointerType === 'pen') return
    closeAfterGrace()
  }

  // Keyed by this item's own DOM id (not entryId directly) so it lines up
  // with `activeDomId` without a re-derivation on every render.
  const itemRefs = useRef<Map<string, HTMLButtonElement>>(new Map())

  // Keeps the active item visible inside the navigator's own scroll region.
  // `block: 'nearest'` is a no-op (no scroll call that actually moves
  // anything) when the item is already fully visible -- the browser itself
  // enforces "only scroll when necessary," not a hand-rolled visibility
  // check here.
  //
  // `isOpen` is a real dependency, not defensive padding: the items only
  // exist (and therefore only register in `itemRefs`) while the panel is
  // open, so an active id that settled *before* the panel was opened --
  // a reader scrolls down to question 40, then opens the list -- never
  // changes afterward, and keying this effect on `activeDomId` alone left
  // the list opening scrolled to the top with the active item far out of
  // view. Re-running on open is what reveals it.
  useEffect(() => {
    if (!isOpen || !activeDomId) return
    itemRefs.current.get(activeDomId)?.scrollIntoView({ block: 'nearest' })
  }, [activeDomId, isOpen])

  if (navigable.length < 2) return null

  const handleSelect = (entryId: string) => {
    const target = document.getElementById(`turn-${entryId}`)
    if (!target) return
    // This app's shell keeps `html`/`body`/`#root` permanently
    // non-scrolling (see index.css) and the conversation's own
    // `overflow-y-auto` region is the only scrollable ancestor above a
    // turn -- so a native `scrollIntoView` here is already guaranteed to
    // move *that* region, never the browser window.
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
  }

  // Escape's dismissal path. Focus may be deep inside the list (a keyboard
  // user tabbing through items), and those nodes unmount on close, so it is
  // returned to the toggle rather than left to fall back to <body>.
  const handleClose = () => {
    cancelPendingClose()
    setIsOpen(false)
    toggleRef.current?.focus()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && isOpen) {
      handleClose()
    }
  }

  return (
    <div
      ref={wrapperRef}
      /* The rail is an overlay pinned to the right margin at every size.
         It used to be `xl`-only, because the old panel opened to the
         *left* of the icon and on a narrow viewport would land on top of
         the answer text. The panel now opens flush to this same right
         edge and clamps its own width to the viewport
         (`w-[calc(100vw-2rem)]` until `sm`), so it no longer has that
         problem -- and the control has to exist on tablets and phones,
         which is where scrolling back through a long conversation by hand
         is worst. The wrapper stays `pointer-events-none` so only the
         real controls take clicks; nothing else about the rail changed. */
      className="pointer-events-none absolute right-4 top-1/2 z-20 flex h-44 w-9 -translate-y-1/2 flex-col items-center"
      onKeyDown={handleKeyDown}
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
    >
      {/* Decorative rail -- a hairline with end-cap dots marking where this
          control sits against an otherwise empty right edge. `aria-hidden`
          since it carries no information a screen reader user needs, and
          deliberately not interactive (the wrapper is
          `pointer-events-none`); the two flex-1 line segments stretch to
          fill whatever space is left once the dots and icon take their
          fixed size, so the icon stays exactly centered regardless of the
          rail's own height. */}
      {/* The rail fades out while the panel is open -- the panel covers the
          same lane, so leaving the hairline visible just showed a seam
          through the icon's gutter. `motion-reduce` defers to the global
          reduced-motion rule in index.css. */}
      <span
        aria-hidden="true"
        className={cn(railSegment, 'h-1.5 w-1.5 shrink-0 rounded-full', isOpen && 'opacity-0')}
      />
      <span aria-hidden="true" className={cn(railSegment, 'w-px flex-1', isOpen && 'opacity-0')} />
      <button
        ref={toggleRef}
        type="button"
        aria-label={t('questionNavigator.toggleLabel')}
        aria-expanded={isOpen}
        aria-haspopup="true"
        onClick={() => (isOpen ? handleClose() : openNow())}
        /* z-30 keeps the icon above the panel, which now extends
           underneath it (see the docstring). The panel reserves this exact
           36px lane with its own `pr-9`, so the icon covers no content. */
        className="pointer-events-auto relative z-30 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--popover-surface)] text-[var(--muted-foreground)] shadow-md transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
      >
        {/* Hamburger/sandwich icon -- three short horizontal bars */}
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M2 4H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M2 8H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M2 12H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      <span aria-hidden="true" className={cn(railSegment, 'w-px flex-1', isOpen && 'opacity-0')} />
      <span
        aria-hidden="true"
        className={cn(railSegment, 'h-1.5 w-1.5 shrink-0 rounded-full', isOpen && 'opacity-0')}
      />

      {isOpen && (
        <nav
          aria-label={t('questionNavigator.label')}
          data-testid="question-navigator-panel"
          /* Anchored `right-0` -- flush to the wrapper's right edge rather
             than beside the icon, so the panel reaches the screen's right
             margin and sits *under* the icon, leaving no gap for a pointer
             to fall through (see the docstring).
             `pr-9` reserves the icon's own 36px lane so no row ever runs
             underneath it.
             Width is clamped to the viewport on phones and steps up with
             the breakpoints; `max-h` keeps a tall list inside the window on
             a short laptop. The open animation is a short fade/slide, and
             `motion-reduce` hands off to index.css's global reduced-motion
             rule. */
          className="animate-flyout-in pointer-events-auto absolute right-0 top-1/2 flex w-[calc(100vw-2rem)] max-w-[calc(100vw-2rem)] -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover-surface)] pr-9 shadow-lg sm:w-80 lg:w-96 xl:w-[26rem]"
        >
          <div className="border-b border-[var(--border)] py-2 pl-3 pr-2">
            <p className="text-xs font-semibold text-[var(--muted-foreground)]">
              {t('questionNavigator.label')}
            </p>
          </div>
          <TooltipProvider delayDuration={300}>
            {/* The one and only scroll region this panel owns, capped at
                ten rows so the panel keeps the same footprint whether the
                conversation has eleven questions or sixty. Everything past
                the tenth scrolls; nothing is dropped from the list.

                The cap is derived, not eyeballed -- one row is the
                `text-xs` line box (1rem) plus this button's `py-1.5`
                (0.375rem x 2), so 1.75rem; ten of those is 17.5rem, and
                `box-border` means this element's own `py-1` (0.5rem)
                counts inside `max-height`, giving 18rem total. Change the
                row's padding or text size and this number has to move with
                it. It is a *maximum*: a three-question list is still three
                rows tall.

                `min(60vh, ...)` is the adaptive half of requirement
                "display 10 questions": ten rows need 288px, which a short
                laptop window or a landscape phone does not have, and on
                those the list takes 60% of the viewport and scrolls
                instead of overflowing off-screen. */}
            {/* `role="list"` below is not redundant: Tailwind preflight
                removes the list marker, and WebKit then drops list
                semantics, so VoiceOver on Safari/iOS stops announcing this
                as a list. See ConversationContextPanel.tsx for the full
                note. */}
            {/* oxlint-disable-next-line jsx-a11y/no-redundant-roles */}
            <ul role="list"
              data-testid="question-navigator-list"
              className="max-h-[min(60vh,18rem)] overflow-y-auto overscroll-contain py-1"
            >
              {navigable.map((entry, index) => {
                const domId = `turn-${entry.entryId}`
                const isActive = activeDomId === domId
                const oneLineQuestion = collapseQuestionWhitespace(entry.question)
                return (
                  <li key={entry.entryId}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          ref={(el) => {
                            if (el) itemRefs.current.set(domId, el)
                            else itemRefs.current.delete(domId)
                          }}
                          type="button"
                          onClick={() => handleSelect(entry.entryId)}
                          aria-current={isActive ? 'true' : undefined}
                          aria-label={t('questionNavigator.itemLabel', {
                            question: oneLineQuestion,
                          })}
                          className={cn(
                            'flex w-full items-center gap-2 border-l-2 px-3 py-1.5 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] focus-visible:ring-inset',
                            isActive
                              ? 'border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--foreground)]'
                              : 'border-transparent text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]',
                          )}
                        >
                          <span
                            aria-hidden="true"
                            className="shrink-0 tabular-nums text-[10px] text-[var(--muted-foreground)]"
                          >
                            {index + 1}
                          </span>
                          <ScrollingText text={oneLineQuestion} className="flex-1" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="left">{entry.question}</TooltipContent>
                    </Tooltip>
                  </li>
                )
              })}
            </ul>
          </TooltipProvider>
        </nav>
      )}
    </div>
  )
}
