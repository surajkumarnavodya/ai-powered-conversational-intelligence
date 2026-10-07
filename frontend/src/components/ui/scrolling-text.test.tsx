import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ScrollingText } from './scrolling-text'

/** jsdom never actually lays out text, so `scrollWidth`/`clientWidth` are
 * both 0 by default on every element -- these helpers stand in for real
 * layout so the component's own overflow-detection logic can be exercised
 * deterministically. Firing a native `resize` event re-triggers the
 * component's own re-measure listener (registered in its layout effect),
 * since the mocked widths are only set *after* the initial, real-widths
 * (0/0) render. */
function mockOverflow(
  outer: HTMLElement,
  inner: HTMLElement,
  { scrollWidth, clientWidth }: { scrollWidth: number; clientWidth: number },
) {
  Object.defineProperty(inner, 'scrollWidth', { configurable: true, value: scrollWidth })
  Object.defineProperty(outer, 'clientWidth', { configurable: true, value: clientWidth })
  fireEvent(window, new Event('resize'))
}

function renderAndGetSpans(text: string) {
  const { container } = render(<ScrollingText text={text} />)
  const outer = container.querySelector('span') as HTMLElement
  const inner = outer.querySelector('span') as HTMLElement
  return { outer, inner }
}

describe('ScrollingText', () => {
  it('renders the full text, never a shortened/truncated version', () => {
    const full = 'A reasonably long label that might or might not actually overflow its box'
    render(<ScrollingText text={full} />)
    expect(screen.getByText(full)).toBeInTheDocument()
  })

  it('never applies a scroll transform when the text already fits its box', () => {
    const { outer, inner } = renderAndGetSpans('Short')
    mockOverflow(outer, inner, { scrollWidth: 50, clientWidth: 100 })

    fireEvent.mouseEnter(outer)
    expect(inner.style.transform).toBe('translateX(0px)')
  })

  it('scrolls left by exactly the overflow amount on hover once the text actually overflows', () => {
    const { outer, inner } = renderAndGetSpans('A very long label that overflows its own box')
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    expect(inner.style.transform).toBe('translateX(-180px)')
  })

  it('snaps back to the start the moment the pointer leaves', () => {
    const { outer, inner } = renderAndGetSpans('A very long label that overflows its own box')
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    expect(inner.style.transform).toBe('translateX(-180px)')

    fireEvent.mouseLeave(outer)
    expect(inner.style.transform).toBe('translateX(0px)')
  })

  it('also scrolls on focus and resets on blur, for keyboard users', () => {
    const { outer, inner } = renderAndGetSpans('A very long label that overflows its own box')
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.focus(outer)
    expect(inner.style.transform).toBe('translateX(-180px)')

    fireEvent.blur(outer)
    expect(inner.style.transform).toBe('translateX(0px)')
  })

  it('uses a slower, linear transition while actively scrolling and a quick ease-out when resetting', () => {
    const { outer, inner } = renderAndGetSpans('A very long label that overflows its own box')
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    expect(inner.style.transition).toContain('linear')

    fireEvent.mouseLeave(outer)
    expect(inner.style.transition).toContain('ease-out')
  })

  it('scales the active scroll duration with how much text needs to be revealed', () => {
    const durationMs = (transition: string) => Number(/(\d+)ms/.exec(transition)?.[1])

    const { outer, inner } = renderAndGetSpans('A very long label that overflows its own box')
    mockOverflow(outer, inner, { scrollWidth: 1000, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    const longDuration = durationMs(inner.style.transition)

    fireEvent.mouseLeave(outer)
    mockOverflow(outer, inner, { scrollWidth: 200, clientWidth: 120 })
    fireEvent.mouseEnter(outer)
    const shortDuration = durationMs(inner.style.transition)

    expect(longDuration).toBeGreaterThan(shortDuration)
  })

  it('clips the text at rest via overflow-hidden on the wrapping box', () => {
    const { container } = render(<ScrollingText text="Anything" />)
    const outer = container.querySelector('span') as HTMLElement
    expect(outer.className).toMatch(/overflow-hidden/)
  })
})
