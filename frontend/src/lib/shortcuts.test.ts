import { afterEach, describe, expect, it } from 'vitest'
import {
  CHAT_COMPOSER_ATTRIBUTE,
  SHORTCUTS,
  focusChatComposer,
  formatShortcut,
  matchesShortcut,
  shouldIgnoreShortcut,
  type ShortcutDefinition,
} from './shortcuts'

function keydown(init: Partial<KeyboardEventInit> & { key: string }): KeyboardEvent {
  return new KeyboardEvent('keydown', init)
}

const byAction = (action: string): ShortcutDefinition => {
  const found = SHORTCUTS.find((s) => s.action === action)
  if (!found) throw new Error(`no shortcut declared for ${action}`)
  return found
}

describe('SHORTCUTS', () => {
  it('declares no duplicate binding', () => {
    const signatures = SHORTCUTS.map((s) => `${s.key}|${Boolean(s.mod)}|${Boolean(s.shift)}`)
    expect(new Set(signatures).size).toBe(SHORTCUTS.length)
  })

  it('gives every shortcut a label key, so none can go undocumented', () => {
    for (const shortcut of SHORTCUTS) {
      expect(shortcut.labelKey).toMatch(/^shortcuts\./)
    }
  })
})

describe('matchesShortcut', () => {
  it('accepts either platform modifier for one binding', () => {
    const toggleSidebar = byAction('toggleSidebar')
    expect(matchesShortcut(keydown({ key: 'b', ctrlKey: true }), toggleSidebar)).toBe(true)
    expect(matchesShortcut(keydown({ key: 'b', metaKey: true }), toggleSidebar)).toBe(true)
  })

  it('requires the modifier when one is declared', () => {
    expect(matchesShortcut(keydown({ key: 'b' }), byAction('toggleSidebar'))).toBe(false)
  })

  it('rejects a modifier when none is declared', () => {
    // Otherwise Ctrl+/ (a browser or extension binding) would steal the
    // composer-focus shortcut.
    expect(matchesShortcut(keydown({ key: '/', ctrlKey: true }), byAction('focusComposer'))).toBe(
      false,
    )
  })

  it('checks Shift only alongside a modifier', () => {
    // `?` is Shift+/ on a US layout and unshifted on others, so the
    // produced character is the only reliable signal.
    const showHelp = byAction('showHelp')
    expect(matchesShortcut(keydown({ key: '?', shiftKey: true }), showHelp)).toBe(true)
    expect(matchesShortcut(keydown({ key: '?', shiftKey: false }), showHelp)).toBe(true)

    // With a modifier held no character is produced, so Shift is explicit.
    const newChat = byAction('newChat')
    expect(matchesShortcut(keydown({ key: 'o', ctrlKey: true, shiftKey: true }), newChat)).toBe(true)
    expect(matchesShortcut(keydown({ key: 'o', ctrlKey: true, shiftKey: false }), newChat)).toBe(
      false,
    )
  })

  it('always rejects Alt, which composes characters on many layouts', () => {
    expect(
      matchesShortcut(keydown({ key: 'b', ctrlKey: true, altKey: true }), byAction('toggleSidebar')),
    ).toBe(false)
  })

  it('is case-insensitive on the produced key', () => {
    expect(
      matchesShortcut(keydown({ key: 'O', ctrlKey: true, shiftKey: true }), byAction('newChat')),
    ).toBe(true)
  })
})

describe('shouldIgnoreShortcut', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it.each(['input', 'textarea', 'select'])('ignores a keystroke inside a <%s>', (tag) => {
    const element = document.createElement(tag)
    document.body.append(element)
    const event = keydown({ key: '/' })
    Object.defineProperty(event, 'target', { value: element })
    expect(shouldIgnoreShortcut(event)).toBe(true)
  })

  it('ignores a keystroke inside a contenteditable', () => {
    const element = document.createElement('div')
    element.contentEditable = 'true'
    // jsdom does not derive isContentEditable from the attribute.
    Object.defineProperty(element, 'isContentEditable', { value: true })
    document.body.append(element)
    const event = keydown({ key: '/' })
    Object.defineProperty(event, 'target', { value: element })
    expect(shouldIgnoreShortcut(event)).toBe(true)
  })

  it('ignores every keystroke while a modal is open', () => {
    document.body.innerHTML = '<div role="dialog" data-state="open"></div>'
    const event = keydown({ key: '/' })
    Object.defineProperty(event, 'target', { value: document.body })
    expect(shouldIgnoreShortcut(event)).toBe(true)
  })

  it('does not ignore a closed dialog left in the tree', () => {
    document.body.innerHTML = '<div role="dialog" data-state="closed"></div>'
    const event = keydown({ key: '/' })
    Object.defineProperty(event, 'target', { value: document.body })
    expect(shouldIgnoreShortcut(event)).toBe(false)
  })

  it('allows an ordinary keystroke on the page body', () => {
    const event = keydown({ key: '/' })
    Object.defineProperty(event, 'target', { value: document.body })
    expect(shouldIgnoreShortcut(event)).toBe(false)
  })
})

describe('formatShortcut', () => {
  it('renders the Apple modifier on an Apple keyboard', () => {
    expect(formatShortcut(byAction('newChat'), true)).toBe('⌘ + Shift + O')
  })

  it('renders Ctrl elsewhere', () => {
    expect(formatShortcut(byAction('newChat'), false)).toBe('Ctrl + Shift + O')
  })

  it('renders a bare key with no modifier', () => {
    expect(formatShortcut(byAction('showHelp'), false)).toBe('?')
    expect(formatShortcut(byAction('focusComposer'), false)).toBe('/')
  })
})

describe('focusChatComposer', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('focuses the marked composer', () => {
    document.body.innerHTML = `<textarea ${CHAT_COMPOSER_ATTRIBUTE}></textarea>`
    focusChatComposer()
    expect(document.activeElement?.tagName).toBe('TEXTAREA')
  })

  it('does nothing on a route with no composer', () => {
    // A no-op, not a crash: most routes in this app have no composer.
    expect(() => focusChatComposer()).not.toThrow()
  })
})
