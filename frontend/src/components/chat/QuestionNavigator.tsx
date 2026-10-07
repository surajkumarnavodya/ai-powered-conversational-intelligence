import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useActiveElementId } from '@/hooks/useActiveElementId'
import type { QueryHistoryEntry } from '@/lib/history'
import { shortenQuestion } from '@/lib/questionLabel'
import { cn } from '@/lib/utils'

/** Compact "jump to question" panel for the current conversation -- renders
 * only once there are at least two navigable questions (one question has
 * nothing to navigate *between*, so showing an empty-feeling single-row
 * panel would just be visual noise -- see the component's own test for
 * this exact "one question" case).
 *
 * Reuses `entry.entryId` (already the conversation's own stable identity --
 * `TurnCard` renders each turn at DOM id `turn-${entryId}`) as the one and
 * only way an item is matched to its message: never question text, never
 * array position. Two entries with byte-identical question text still get
 * two independent entryIds and therefore navigate to two independent
 * elements.
 *
 * Deliberately hidden below the `2xl` breakpoint (1536px), not a smaller,
 * more tempting one -- worked out from this app's own real layout, not
 * guessed: the chat column's own hard ceiling is `max-w-4xl` (896px) plus
 * its `px-4` padding (32px) = 928px, the sidebar's own *expanded* width is
 * `lg:w-72` (288px), and this panel is `w-64` (256px) plus a `right-4`
 * margin (16px) and a little breathing room (~16px) = ~288px. A viewport
 * narrower than roughly 928 + 288 + 288 ≈ 1504px cannot fit all three
 * without the panel overlapping the chat column's own right edge whenever
 * that column is actually rendered at its full width (a wide table or
 * chart) -- 1536px is the nearest real Tailwind breakpoint at or above
 * that figure, not an arbitrary round number. Below it, the requirement
 * that the navigator "must not push or resize the answer area" rules out
 * the other fix (a true second column that shrinks the chat to make
 * room), so the deliberate, disclosed choice is to not show it there at
 * all rather than risk covering real content. This app's own small-screen
 * pattern for secondary navigation (MobileNav's drawer) is a larger,
 * separate mechanism than this one panel needs.
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

  return (
    <nav
      aria-label={t('questionNavigator.label')}
      className="absolute right-4 top-4 z-10 hidden w-64 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-md 2xl:flex"
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
  )
}
