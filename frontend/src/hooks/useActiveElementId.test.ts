import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useActiveElementId } from './useActiveElementId'

/** A controllable stand-in for the real `IntersectionObserver` -- captures
 * the callback passed to its constructor and every observed element, so a
 * test can simulate "this element just crossed the watched band" directly
 * instead of needing a real layout engine. Replaces the global for the
 * duration of each test and is restored afterward. */
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = []
  readonly callback: IntersectionObserverCallback
  readonly observed = new Set<Element>()
  disconnected = false

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeIntersectionObserver.instances.push(this)
  }

  observe(el: Element) {
    this.observed.add(el)
  }

  unobserve(el: Element) {
    this.observed.delete(el)
  }

  disconnect() {
    this.disconnected = true
  }

  takeRecords(): IntersectionObserverEntry[] {
    return []
  }

  /** Fires the callback as if `targetId` just became (or stopped being)
   * intersecting -- the one thing a real browser would otherwise have to
   * do via actual scrolling/layout. */
  fire(targetId: string, isIntersecting: boolean) {
    const target = document.getElementById(targetId)
    if (!target) throw new Error(`no element with id ${targetId} in the document`)
    this.callback(
      [{ target, isIntersecting } as unknown as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    )
  }
}

function makeElements(ids: string[]): HTMLDivElement {
  const container = document.createElement('div')
  for (const id of ids) {
    const el = document.createElement('div')
    el.id = id
    container.appendChild(el)
  }
  document.body.appendChild(container)
  return container
}

let originalIntersectionObserver: typeof IntersectionObserver

beforeEach(() => {
  FakeIntersectionObserver.instances = []
  originalIntersectionObserver = window.IntersectionObserver
  window.IntersectionObserver = FakeIntersectionObserver as unknown as typeof IntersectionObserver
})

afterEach(() => {
  window.IntersectionObserver = originalIntersectionObserver
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('useActiveElementId', () => {
  it('starts with no active id before anything has been observed', () => {
    const container = makeElements(['turn-1', 'turn-2'])
    const { result } = renderHook(() => useActiveElementId(['turn-1', 'turn-2'], { current: container }))

    expect(result.current).toBeNull()
  })

  it('becomes the id that crosses the watched band', () => {
    const container = makeElements(['turn-1', 'turn-2'])
    const { result } = renderHook(() => useActiveElementId(['turn-1', 'turn-2'], { current: container }))

    act(() => {
      FakeIntersectionObserver.instances[0].fire('turn-1', true)
    })

    expect(result.current).toBe('turn-1')
  })

  it('picks the last-in-order id among several simultaneously intersecting', () => {
    const container = makeElements(['turn-1', 'turn-2', 'turn-3'])
    const { result } = renderHook(() =>
      useActiveElementId(['turn-1', 'turn-2', 'turn-3'], { current: container }),
    )

    act(() => {
      const observer = FakeIntersectionObserver.instances[0]
      observer.fire('turn-1', true)
      observer.fire('turn-2', true)
    })

    expect(result.current).toBe('turn-2')
  })

  it('keeps the last known active id once nothing currently intersects, instead of clearing it', () => {
    const container = makeElements(['turn-1', 'turn-2'])
    const { result } = renderHook(() => useActiveElementId(['turn-1', 'turn-2'], { current: container }))

    act(() => {
      FakeIntersectionObserver.instances[0].fire('turn-1', true)
    })
    expect(result.current).toBe('turn-1')

    act(() => {
      FakeIntersectionObserver.instances[0].fire('turn-1', false)
    })
    expect(result.current).toBe('turn-1')
  })

  it('updates to a later id once the earlier one scrolls out and a later one scrolls in', () => {
    const container = makeElements(['turn-1', 'turn-2'])
    const { result } = renderHook(() => useActiveElementId(['turn-1', 'turn-2'], { current: container }))

    act(() => {
      const observer = FakeIntersectionObserver.instances[0]
      observer.fire('turn-1', true)
      observer.fire('turn-1', false)
      observer.fire('turn-2', true)
    })

    expect(result.current).toBe('turn-2')
  })

  it('disconnects the observer on unmount', () => {
    const container = makeElements(['turn-1'])
    const { unmount } = renderHook(() => useActiveElementId(['turn-1'], { current: container }))
    const observer = FakeIntersectionObserver.instances[0]

    unmount()

    expect(observer.disconnected).toBe(true)
  })

  it('re-observes when the id list changes (a question added or removed)', () => {
    const container = makeElements(['turn-1', 'turn-2'])
    const { rerender } = renderHook(({ ids }: { ids: string[] }) => useActiveElementId(ids, { current: container }), {
      initialProps: { ids: ['turn-1'] },
    })
    expect(FakeIntersectionObserver.instances[0].observed.size).toBe(1)

    rerender({ ids: ['turn-1', 'turn-2'] })

    expect(FakeIntersectionObserver.instances[0].disconnected).toBe(true)
    expect(FakeIntersectionObserver.instances[1].observed.size).toBe(2)
  })

  it('does nothing when there is no container yet', () => {
    const { result } = renderHook(() => useActiveElementId(['turn-1'], { current: null }))
    expect(result.current).toBeNull()
    expect(FakeIntersectionObserver.instances).toHaveLength(0)
  })

  it('is tolerant of an id with no matching element in the document', () => {
    const container = makeElements(['turn-1'])
    expect(() =>
      renderHook(() => useActiveElementId(['turn-1', 'turn-does-not-exist'], { current: container })),
    ).not.toThrow()
    expect(FakeIntersectionObserver.instances[0].observed.size).toBe(1)
  })
})
