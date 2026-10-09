import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, RefObject } from 'react'
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
 * **The panel stays open until it is explicitly closed.** There is
 * deliberately no hover-to-open and no auto-hide: it closes only on the
 * panel's own close button, on the toggle icon, or on Escape. The
 * earlier version opened on `mouseenter` and closed on `mouseleave` of
 * the wrapper, which made the panel genuinely unusable in practice --
 * the panel sits to the *left* of the icon with an 8px gap (`mr-2`)
 * between them, and that gap belongs to neither the wrapper's own box
 * nor any descendant, so a pointer travelling from the icon to the list
 * crossed it, fired `mouseleave`, and closed the panel before it could
 * be reached. Closing on `blur` had the same effect for keyboard users.
 * An explicit close is also simply the right affordance for a panel a
 * reader scrolls through and clicks items in.
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
 * at rest, scrolling left on hover/focus to reveal the rest once it's
 * actually long enough to overflow (Perplexity's own treatment for a
 * long history-list title). The accessible tooltip below is independent
 * of this and unchanged: a screen reader or a sighted keyboard user who
 * never triggers the hover animation still gets the complete question
 * through it.
 */
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
  const navigable = entries.filter((entry) => entry.question.trim() !== '')
  const domIds = navigable.map((entry) => `turn-${entry.entryId}`)
  const activeDomId = useActiveElementId(domIds, scrollContainerRef)

  const [isOpen, setIsOpen] = useState(false)
  // Focus returns here whenever the panel is dismissed, so closing it from
  // the panel's own close button (or with Escape) never strands focus on a
  // node that has just been unmounted.
  const toggleRef = useRef<HTMLButtonElement>(null)

  // Keyed by this item's own DOM id (not entryId directly) so it lines up
  // with `activeDomId` without a re-derivation on every render.
  const itemRefs = useRef<Map<string, HTMLButtonElement>>(new Map())

  // Keeps the active item visible inside the navigator's own scroll region.
  // `block: 'nearest'` is a no-op (no scroll call that actually moves
  // anything) when the item is already fully visible -- the browser itself
  // enforces "only scroll when necessary," not a hand-rolled visibility
  // check here.
  useEffect(() => {
    if (!activeDomId) return
    itemRefs.current.get(activeDomId)?.scrollIntoView({ block: 'nearest' })
  }, [activeDomId])

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

  // The dismissal path for the two actions that close the panel from
  // somewhere other than the toggle itself (the panel's close button and
  // Escape), so focus handling cannot diverge between them. The toggle
  // needs no such restore -- focus is already on it.
  const handleClose = () => {
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
      className="pointer-events-none absolute right-4 top-1/2 z-20 flex h-44 w-9 -translate-y-1/2 flex-col items-center"
      onKeyDown={handleKeyDown}
    >
      {/* Decorative rail -- a hairline with end-cap dots marking where this
          control sits against an otherwise empty right edge. `aria-hidden`
          since it carries no information a screen reader user needs, and
          deliberately not interactive (the wrapper is
          `pointer-events-none`); the two flex-1 line segments stretch to
          fill whatever space is left once the dots and icon take their
          fixed size, so the icon stays exactly centered regardless of the
          rail's own height. */}
      <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--border)]" />
      <span aria-hidden="true" className="w-px flex-1 bg-[var(--border)]" />
      <button
        ref={toggleRef}
        type="button"
        aria-label={t('questionNavigator.toggleLabel')}
        aria-expanded={isOpen}
        aria-haspopup="true"
        onClick={() => setIsOpen((prev) => !prev)}
        className="pointer-events-auto relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--popover-surface)] text-[var(--muted-foreground)] shadow-md transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        {/* Hamburger/sandwich icon -- three short horizontal bars */}
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M2 4H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M2 8H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M2 12H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      <span aria-hidden="true" className="w-px flex-1 bg-[var(--border)]" />
      <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--border)]" />

      {isOpen && (
        <nav
          aria-label={t('questionNavigator.label')}
          className="pointer-events-auto absolute right-full top-1/2 mr-2 flex w-64 max-w-[calc(100vw-5rem)] -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover-surface)] shadow-lg"
        >
          {/* `justify-between` is what puts the close control hard against
              the panel's right edge, opposite the title -- the conventional
              place to look for it, and the only way to dismiss this panel
              with a pointer besides the toggle icon. */}
          <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] py-2 pl-3 pr-2">
            <p className="text-xs font-semibold text-[var(--muted-foreground)]">
              {t('questionNavigator.label')}
            </p>
            <button
              type="button"
              aria-label={t('questionNavigator.closeLabel')}
              onClick={handleClose}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {/* Close (X) icon */}
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path
                  d="M3.5 3.5L10.5 10.5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
                <path
                  d="M10.5 3.5L3.5 10.5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>
          <TooltipProvider delayDuration={300}>
            {/* The one and only scroll region this panel owns -- bounded so the
                panel itself never grows past a compact footprint. The formula
                scales down on a short viewport (60% of it) but is capped at
                26rem so the panel stays "compact" even on a very tall monitor,
                per the feature's own requirement -- not a fixed pixel height
                that would either overflow a small laptop window or look
                awkwardly tiny on a 4K display. */}
            <ul
              role="list"
              className="max-h-[min(60vh,26rem)] overflow-y-auto overscroll-contain py-1"
            >
              {navigable.map((entry, index) => {
                const domId = `turn-${entry.entryId}`
                const isActive = activeDomId === domId
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
                          className={cn(
                            'flex w-full items-center gap-2 border-l-2 px-3 py-1.5 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-inset',
                            isActive
                              ? 'border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--foreground)]'
                              : 'border-transparent text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]',
                          )}
                        >
                          <span className="shrink-0 tabular-nums text-[10px] text-[var(--muted-foreground)]">
                            {index + 1}
                          </span>
                          <ScrollingText
                            text={collapseQuestionWhitespace(entry.question)}
                            className="flex-1"
                          />
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
