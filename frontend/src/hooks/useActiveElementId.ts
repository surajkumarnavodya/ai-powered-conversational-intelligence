import { useEffect, useRef, useState, type RefObject } from 'react'

/** Tracks which of several elements (each looked up by `document.getElementById(id)`)
 * is "currently being read" inside a scroll container -- the standard
 * scrollspy technique: an `IntersectionObserver` rooted at the container
 * watches a thin band near its top edge (`rootMargin`'s negative bottom
 * value), so an element counts as active once it has scrolled up to meet
 * that band, not merely whenever any sliver of it is visible anywhere on
 * screen. When several are "active" at once (short entries stacked close
 * together), the last one in DOM order wins -- the one most recently
 * scrolled to that line is the one the user is currently at.
 *
 * Deliberately not a per-scroll-event calculation: the observer only fires
 * when an element actually crosses the watched band, so scrolling through a
 * long conversation costs a handful of callback invocations, not one
 * per `scroll` event.
 *
 * @param ids Element ids, in the same order they appear in the document.
 *   Re-observes automatically when this list changes (a question added or
 *   removed, or a conversation switch).
 * @param containerRef The scrollable ancestor to root the observer in --
 *   required, so this never falls back to treating the whole browser
 *   viewport as the scroll region.
 * @returns The active id, or `null` before anything has been observed yet
 *   (not cleared back to `null` just because nothing currently crosses the
 *   band -- the last known active id is kept, so the indicator never
 *   flickers off while scrolling through a gap).
 */
export function useActiveElementId(ids: string[], containerRef: RefObject<HTMLElement | null>): string | null {
  const [activeId, setActiveId] = useState<string | null>(null)
  // The intersecting set, in the same order `ids` was observed -- read inside
  // the observer callback without re-subscribing it on every change.
  const intersecting = useRef<Set<string>>(new Set())

  // A plain, cheap string key: re-runs the effect exactly when the *set* of
  // tracked ids changes (add/remove/reorder), not on every re-render where
  // the caller happens to pass a fresh array instance with the same
  // contents.
  const idsKey = ids.join('\u0000')

  useEffect(() => {
    const root = containerRef.current
    if (!root || ids.length === 0) return

    const elements = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null)
    if (elements.length === 0) return

    intersecting.current = new Set()

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = entry.target.id
          if (entry.isIntersecting) intersecting.current.add(id)
          else intersecting.current.delete(id)
        }
        // Last in DOM order among those currently crossing the band --
        // elements are observed in `ids`' own order, which already matches
        // document order (the navigator and the conversation render from
        // the same `queryHistory` list).
        for (let i = ids.length - 1; i >= 0; i--) {
          if (intersecting.current.has(ids[i])) {
            setActiveId((current) => (current === ids[i] ? current : ids[i]))
            return
          }
        }
        // Nothing currently crosses the band (e.g. a short conversation
        // that doesn't fill the container) -- keep the last known value
        // rather than clearing it.
      },
      {
        root,
        // Treat an element as "active" once it reaches the top 25% of the
        // container, and stop counting it once it's scrolled past the
        // bottom 65% -- a generous middle band that tolerates short and
        // tall turns alike without needing per-turn measurement.
        rootMargin: '0px 0px -65% 0px',
        threshold: 0,
      },
    )

    for (const el of elements) observer.observe(el)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- idsKey is the real dependency; ids/containerRef are read fresh each run
  }, [idsKey, containerRef])

  return activeId
}
