/** The application's global keyboard shortcuts, declared once.
 *
 * One owner for the list (`CLAUDE.md` 4.8): the handler in
 * `hooks/useGlobalShortcuts.ts` and the help dialog in
 * `components/layout/ShortcutHelpDialog.tsx` both read this array, so a
 * shortcut cannot work while going undocumented, or be documented while
 * doing nothing.
 *
 * Before this existed the app had keyboard handling only inside
 * individual components -- Enter in the composer, arrows in the question
 * navigator, Escape in dialogs. Those stay exactly as they are; this adds
 * a document-level layer above them and deliberately never competes with
 * them, because it refuses to fire while a text field has focus.
 */

/** What a shortcut asks the app to do. Named actions rather than
 * callbacks, so the list stays serialisable data that the help dialog can
 * render without holding any behaviour. */
export type ShortcutAction =
  | 'newChat'
  | 'toggleSidebar'
  | 'focusComposer'
  | 'openSettings'
  | 'showHelp'

export interface ShortcutDefinition {
  action: ShortcutAction
  /** Matched against a lower-cased `KeyboardEvent.key`. */
  key: string
  /** Ctrl on Windows/Linux, Command on macOS -- one binding, both
   * platforms, because `event.metaKey || event.ctrlKey` is what users of
   * each actually press. */
  mod?: boolean
  /** Only meaningful alongside `mod` -- see `matchesShortcut`. */
  shift?: boolean
  /** i18n key for the human description. */
  labelKey: string
}

/** Chosen to avoid colliding with anything a browser or screen reader
 * already claims, and to match the conventions people arrive with:
 * Ctrl/Cmd+Shift+O for a new chat, Ctrl/Cmd+B for the side panel,
 * Ctrl/Cmd+, for settings, `/` to jump to the input, `?` for help. */
export const SHORTCUTS: readonly ShortcutDefinition[] = [
  { action: 'newChat', key: 'o', mod: true, shift: true, labelKey: 'shortcuts.newChat' },
  { action: 'toggleSidebar', key: 'b', mod: true, labelKey: 'shortcuts.toggleSidebar' },
  { action: 'openSettings', key: ',', mod: true, labelKey: 'shortcuts.openSettings' },
  { action: 'focusComposer', key: '/', labelKey: 'shortcuts.focusComposer' },
  { action: 'showHelp', key: '?', labelKey: 'shortcuts.showHelp' },
] as const

/** Marks the chat composer so `focusComposer` can find it.
 *
 * A data attribute rather than a shared ref: `AppShell` renders the
 * composer through `<Outlet />`, several routes deep, so threading a ref
 * up to the shell would mean a context provider and a store field for one
 * focus call. The attribute is inert if the composer is not on screen --
 * the lookup simply finds nothing and the shortcut does nothing, which is
 * the correct behaviour on a route that has no composer.
 */
export const CHAT_COMPOSER_ATTRIBUTE = 'data-chat-composer'

/** Focuses the chat composer if one is on screen. No-op otherwise. */
export function focusChatComposer(): void {
  const composer = document.querySelector<HTMLTextAreaElement>(`[${CHAT_COMPOSER_ATTRIBUTE}]`)
  composer?.focus()
}

/** True on macOS, where the modifier renders as ⌘ rather than Ctrl.
 *
 * `navigator.platform` is deprecated, so this reads the user agent. A
 * wrong guess costs a cosmetically wrong label in the help dialog and
 * nothing else -- the matcher accepts **either** modifier regardless, so
 * the shortcut still works.
 */
export function isAppleKeyboard(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Mac|iPhone|iPad|iPod/.test(navigator.userAgent)
}

/** The shortcut rendered for display, e.g. `"Ctrl + Shift + O"`. */
export function formatShortcut(shortcut: ShortcutDefinition, apple = isAppleKeyboard()): string {
  const parts: string[] = []
  if (shortcut.mod) parts.push(apple ? '⌘' : 'Ctrl')
  if (shortcut.mod && shortcut.shift) parts.push('Shift')
  parts.push(shortcut.key.length === 1 ? shortcut.key.toUpperCase() : shortcut.key)
  return parts.join(' + ')
}

/** Whether `event` is this shortcut.
 *
 * **Why `shift` is only checked when `mod` is set.** Without a modifier,
 * the character the keyboard produced has *already* absorbed Shift: `?`
 * is Shift+/ on a US layout and an unshifted key on several others.
 * Requiring `shiftKey` would break the non-US layouts, and forbidding it
 * would break the US one, so for an unmodified shortcut only the produced
 * character is compared. With a modifier held, no character is produced
 * and Shift has to be checked explicitly.
 *
 * Alt is always rejected: it composes characters on many layouts, and a
 * binding that fired mid-composition would be unusable for anyone typing
 * an accented character.
 */
export function matchesShortcut(event: KeyboardEvent, shortcut: ShortcutDefinition): boolean {
  if (event.altKey) return false
  if (Boolean(shortcut.mod) !== (event.metaKey || event.ctrlKey)) return false
  if (shortcut.mod && Boolean(shortcut.shift) !== event.shiftKey) return false
  return event.key.toLowerCase() === shortcut.key
}

/** Whether a keystroke should be left entirely alone.
 *
 * Two cases, and both are the reason a bare `/` can be a shortcut at all:
 *
 * - **Something is being typed.** An input, textarea, select or
 *   contenteditable has focus, so the keystroke belongs to it. This is
 *   also what keeps the composer's own Enter/Shift+Enter handling and the
 *   question navigator's arrow keys working untouched.
 * - **A modal is open.** Radix renders an open dialog with
 *   `data-state="open"`; while one is up, the shell behind it is `inert`
 *   and a global binding firing into it would act on a surface the person
 *   cannot see.
 */
export function shouldIgnoreShortcut(event: KeyboardEvent): boolean {
  const target = event.target
  if (target instanceof HTMLElement) {
    if (target.isContentEditable) return true
    const tag = target.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  }
  if (typeof document !== 'undefined') {
    if (document.querySelector('[role="dialog"][data-state="open"]')) return true
  }
  return false
}
