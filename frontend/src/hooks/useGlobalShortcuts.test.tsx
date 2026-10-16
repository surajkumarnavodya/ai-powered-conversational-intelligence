import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useGlobalShortcuts } from './useGlobalShortcuts'
import type { ShortcutAction } from '@/lib/shortcuts'

function Harness({ handlers }: { handlers: Partial<Record<ShortcutAction, () => void>> }) {
  useGlobalShortcuts(handlers)
  return <div>harness</div>
}

/** Dispatches a real keydown on `document` and reports whether anything
 * called `preventDefault` -- the signal that the app claimed the key. */
function press(init: Partial<KeyboardEventInit> & { key: string }): boolean {
  const event = new KeyboardEvent('keydown', { ...init, bubbles: true, cancelable: true })
  document.dispatchEvent(event)
  return event.defaultPrevented
}

describe('useGlobalShortcuts', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('runs the handler for a declared shortcut', () => {
    const toggleSidebar = vi.fn()
    render(<Harness handlers={{ toggleSidebar }} />)

    expect(press({ key: 'b', ctrlKey: true })).toBe(true)
    expect(toggleSidebar).toHaveBeenCalledTimes(1)
  })

  it('leaves an unclaimed keystroke completely alone', () => {
    const toggleSidebar = vi.fn()
    render(<Harness handlers={{ toggleSidebar }} />)

    // Ctrl+P must still reach the browser's print dialog.
    expect(press({ key: 'p', ctrlKey: true })).toBe(false)
    expect(toggleSidebar).not.toHaveBeenCalled()
  })

  it('does not swallow a declared shortcut the caller supplied no handler for', () => {
    render(<Harness handlers={{}} />)
    expect(press({ key: 'b', ctrlKey: true })).toBe(false)
  })

  it('does not fire while a text field has focus', () => {
    const focusComposer = vi.fn()
    render(<Harness handlers={{ focusComposer }} />)

    const input = document.createElement('input')
    document.body.append(input)
    input.focus()
    input.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true, cancelable: true }))

    // This is what makes a bare "/" safe to bind at all.
    expect(focusComposer).not.toHaveBeenCalled()
  })

  it('does not fire while a modal is open', () => {
    const newChat = vi.fn()
    render(<Harness handlers={{ newChat }} />)
    document.body.innerHTML += '<div role="dialog" data-state="open"></div>'

    expect(press({ key: 'o', ctrlKey: true, shiftKey: true })).toBe(false)
    expect(newChat).not.toHaveBeenCalled()
  })

  it('detaches its listener on unmount', () => {
    const toggleSidebar = vi.fn()
    const { unmount } = render(<Harness handlers={{ toggleSidebar }} />)
    unmount()

    expect(press({ key: 'b', ctrlKey: true })).toBe(false)
    expect(toggleSidebar).not.toHaveBeenCalled()
  })

  it('calls the latest handler after a re-render, without double-binding', () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = render(<Harness handlers={{ toggleSidebar: first }} />)
    rerender(<Harness handlers={{ toggleSidebar: second }} />)

    press({ key: 'b', ctrlKey: true })

    // The ref is why: a listener re-attached per render would fire the
    // handler more than once, and a stale closure would call `first`.
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })
})
