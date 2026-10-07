import { act, fireEvent, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useScrollToBottom } from './useScrollToBottom'

/** jsdom never actually lays out content, so `scrollHeight`/`scrollTop`/
 * `clientHeight` are all 0 by default -- this stands in for real layout,
 * the same `Object.defineProperty` pattern `scrolling-text.test.tsx`
 * already uses for its own overflow measurement. */
function mockScrollState(
  el: HTMLElement,
  { scrollHeight, scrollTop, clientHeight }: { scrollHeight: number; scrollTop: number; clientHeight: number },
) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight })
  Object.defineProperty(el, 'scrollTop', { configurable: true, value: scrollTop })
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight })
}

function setup() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const containerRef = { current: el }
  const rendered = renderHook(() => useScrollToBottom(containerRef))
  return { el, ...rendered }
}

beforeEach(() => {
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn((cb: FrameRequestCallback) => {
      cb(0)
      return 0
    }),
  )
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('useScrollToBottom', () => {
  it('starts (and stays) at bottom when the content already fits, with no scrollable overflow at all', () => {
    const { el, result } = setup()
    mockScrollState(el, { scrollHeight: 400, scrollTop: 400, clientHeight: 400 })
    fireEvent.scroll(el)
    expect(result.current.isAtBottom).toBe(true)
  })

  it('reports not-at-bottom once scrolled away from the bottom by more than the threshold', () => {
    const { el, result } = setup()
    mockScrollState(el, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
    act(() => {
      fireEvent.scroll(el)
    })
    expect(result.current.isAtBottom).toBe(false)
    expect(result.current.isAtBottomRef.current).toBe(false)
  })

  it('tolerates a small fractional remainder within the threshold, without flickering to not-at-bottom', () => {
    const { el, result } = setup()
    // scrollHeight - scrollTop - clientHeight == 12px, under the 20px default threshold.
    mockScrollState(el, { scrollHeight: 2000, scrollTop: 1388, clientHeight: 600 })
    act(() => {
      fireEvent.scroll(el)
    })
    expect(result.current.isAtBottom).toBe(true)
  })

  it('re-measures via ResizeObserver even with no scroll event at all', () => {
    const { el, result } = setup()
    mockScrollState(el, { scrollHeight: 400, scrollTop: 400, clientHeight: 400 })
    expect(result.current.isAtBottom).toBe(true)

    // Content grew below the fold (e.g. a chart finishing its render) with
    // no scroll event -- scrollTop itself didn't move.
    act(() => {
      mockScrollState(el, { scrollHeight: 900, scrollTop: 400, clientHeight: 400 })
      // The test setup's ResizeObserver is a real browser-API no-op in
      // jsdom (see test/setup.ts) -- drive its callback directly via the
      // same `scroll` event path instead, which this hook treats
      // identically (both ultimately call the same `measure`).
      fireEvent.scroll(el)
    })
    expect(result.current.isAtBottom).toBe(false)
  })

  it('scrollToBottom scrolls the container to its own scrollHeight', () => {
    const { el, result } = setup()
    mockScrollState(el, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
    const scrollToSpy = vi.spyOn(el, 'scrollTo')

    act(() => {
      result.current.scrollToBottom()
    })

    expect(scrollToSpy).toHaveBeenCalledWith({ top: 2000, behavior: 'smooth' })
  })

  it('scrollToBottom respects an explicit behavior override (e.g. an instant jump)', () => {
    const { el, result } = setup()
    mockScrollState(el, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
    const scrollToSpy = vi.spyOn(el, 'scrollTo')

    act(() => {
      result.current.scrollToBottom('auto')
    })

    expect(scrollToSpy).toHaveBeenCalledWith({ top: 2000, behavior: 'auto' })
  })

  it('scrollToBottom uses an instant jump for prefers-reduced-motion users when no behavior is given', () => {
    const { el, result } = setup()
    mockScrollState(el, { scrollHeight: 2000, scrollTop: 200, clientHeight: 600 })
    const scrollToSpy = vi.spyOn(el, 'scrollTo')
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: true,
      media: '',
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList)

    act(() => {
      result.current.scrollToBottom()
    })

    expect(scrollToSpy).toHaveBeenCalledWith({ top: 2000, behavior: 'auto' })
  })
})
