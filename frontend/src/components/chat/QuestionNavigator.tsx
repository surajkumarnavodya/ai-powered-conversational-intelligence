import { useEffect, useRef, useState } from 'react'
import type { FocusEvent, KeyboardEvent, RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useActiveElementId } from '@/hooks/useActiveElementId'
import type { QueryHistoryEntry } from '@/lib/history'
import { shortenQuestion } from '@/lib/questionLabel'
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
 * default state it occupies only the icon's own small footprint, not a
 * fixed-width column. Hovering or focusing the control expands a flyout
 * panel with the full question list, positioned to the *left* of the
 * icon as an absolutely-positioned overlay (so expanding it still never
 * shifts or resizes the chat/answer area underneath) -- it collapses
 * again on mouse-leave or on focus moving outside the control, and
 * Escape closes it immediately for keyboard users. The flyout is only
 * ever mounted in the DOM while open (not merely visually hidden), which
 * is what keeps it genuinely absent from the accessibility tree (and
 * from this component's own tests) until it's actually open.
 *
 * Reuses `entry.entryId` (already the conversation's own stable identity
 * -- `TurnCard` renders each turn at DOM id `turn-${entryId}`) as the one
 * and only way an item is matched to its message: never question text,
 * never array position. Two entries with byte-identical question text
 * still get two independent entryIds and therefore navigate to two
 * independent elements.
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
  const wrapperRef = useRef<HTMLDivElement>(null)

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

  // Closes the flyout only when focus actually leaves the whole control
  // (icon + panel) -- moving focus from the icon to an item inside the
  // panel, or between items, must not close it mid-interaction.
  const handleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (!wrapperRef.current?.contains(event.relatedTarget as Node | null)) {
      setIsOpen(false)
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      setIsOpen(false)
      ;(document.activeElement as HTMLElement | null)?.blur?.()
    }
  }

  return (
    <div
      ref={wrapperRef}
      className="absolute right-4 top-1/2 z-20 -translate-y-1/2"
      onMouseEnter={() => setIsOpen(true)}
      onMouseLeave={() => setIsOpen(false)}
      onFocus={() => setIsOpen(true)}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
    >
      <button
        type="button"
        aria-label={t('questionNavigator.toggleLabel')}
        aria-expanded={isOpen}
        aria-haspopup="true"
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] shadow-md transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        {/* Hamburger/sandwich icon -- three short horizontal bars */}
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="M2 4H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M2 8H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <path d="M2 12H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      {isOpen && (
        <nav
          aria-label={t('questionNavigator.label')}
          className="absolute right-full top-1/2 mr-2 flex w-64 max-w-[calc(100vw-5rem)] -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-lg"
        >
          <p className="border-b border-[var(--border)] px-3 py-2 text-xs font-semibold text-[var(--muted-foreground)]">
            {t('questionNavigator.label')}
          </p>
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
                          <span className="truncate">{shortenQuestion(entry.question)}</span>
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
