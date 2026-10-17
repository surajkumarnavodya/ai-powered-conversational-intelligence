import { useEffect, useRef } from 'react'
import {
  SHORTCUTS,
  matchesShortcut,
  shouldIgnoreShortcut,
  type ShortcutAction,
} from '@/lib/shortcuts'

/** Registers the application's global keyboard shortcuts.
 *
 * Mounted exactly once, in `AppShell`. One `keydown` listener on
 * `document`, removed on unmount -- a second mount would double-fire
 * every binding, which is why this is not a component anyone can drop in
 * twice.
 *
 * `preventDefault` is called **only** on a keystroke that matched a
 * declared shortcut. Everything else passes through untouched, including
 * every browser and assistive-technology binding this app has no business
 * claiming.
 *
 * The handlers are held in a ref so the listener is attached once rather
 * than re-attached on every render. `AppShell` passes inline closures over
 * `navigate` and store actions; without the ref, each render would detach
 * and re-attach the listener, and a keystroke arriving mid-render could
 * land on no listener at all.
 */
export function useGlobalShortcuts(handlers: Partial<Record<ShortcutAction, () => void>>): void {
  const handlersRef = useRef(handlers)
  // Updated in an effect rather than during render: a ref write during
  // render is a side effect in the render phase, which React's own
  // guidance (and `react(refs)` in this repo's linter) rejects. The
  // one-render lag this introduces is immaterial here -- a keystroke
  // arriving between a render and its effect would use the previous
  // handler, which is the same closure over the same store actions.
  useEffect(() => {
    handlersRef.current = handlers
  })

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (shouldIgnoreShortcut(event)) return
      for (const shortcut of SHORTCUTS) {
        if (!matchesShortcut(event, shortcut)) continue
        const handler = handlersRef.current[shortcut.action]
        // A declared shortcut whose handler this caller did not supply is
        // not "handled": fall through and leave the keystroke alone
        // rather than silently swallowing it.
        if (!handler) return
        event.preventDefault()
        handler()
        return
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])
}
