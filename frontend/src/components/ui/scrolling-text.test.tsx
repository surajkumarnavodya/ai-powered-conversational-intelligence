import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ScrollingText } from './scrolling-text'

/** jsdom never actually lays out text, so `scrollWidth`/`clientWidth` are
 * both 0 by default on every element -- these helpers stand in for real
 * layout so the component's own overflow-detection logic can be exercised
 * deterministically. Firing a native `resize` event re-triggers the
 * component's own re-measure listener (registered in its layout effect),
 * since the mocked widths are only set *after* the initial, real-widths
 * (0/0) render. The `ResizeObserver` the component also registers is the
 * global stub from `src/test/setup.ts`, which never invokes its callback,
 * so `resize` stays the deterministic way to drive a re-measure here. */
function mockOverflow(
  outer: HTMLElement,
  inner: HTMLElement,
  { scrollWidth, clientWidth }: { scrollWidth: number; clientWidth: number },
) {
  Object.defineProperty(inner, 'scrollWidth', { configurable: true, value: scrollWidth })
  Object.defineProperty(outer, 'clientWidth', { configurable: true, value: clientWidth })
  fireEvent(window, new Event('resize'))
}

function renderScrollingText(text: string) {
  const { container } = render(<ScrollingText text={text} />)
  const outer = container.querySelector('span') as HTMLElement
  const inner = outer.querySelector('span') as HTMLElement
  return { outer, inner, container }
}

/** The two-copy track only exists while actively scrolling. */
function getTrack(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[data-testid="marquee-track"]')
}

const LONG = 'A question long enough to overflow its own box'

describe('ScrollingText', () => {
  it('renders the full text, never a shortened/truncated version', () => {
    const full = 'A reasonably long label that might or might not actually overflow its box'
    render(<ScrollingText text={full} />)
    expect(screen.getByText(full)).toBeInTheDocument()
  })

  it('exposes the full text as a native tooltip, so it is readable without waiting out a scroll', () => {
    // Also the reduced-motion path: the global `prefers-reduced-motion`
    // rule freezes the marquee, so the tooltip is how the rest of a
    // clipped label stays reachable for those users.
    const { outer } = renderScrollingText(LONG)
    expect(outer).toHaveAttribute('title', LONG)
  })

  it('never scrolls when the text already fits its box', () => {
    const { outer, inner, container } = renderScrollingText('Short')
    mockOverflow(outer, inner, { scrollWidth: 50, clientWidth: 100 })

    fireEvent.mouseEnter(outer)
    expect(getTrack(container)).toBeNull()
  })

  it('loops continuously while hovered once the text actually overflows', () => {
    const { outer, inner, container } = renderScrollingText(LONG)
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    const track = getTrack(container)
    // `.marquee-track` carries `animation: marquee-loop linear infinite`
    // in index.css -- an infinite cycle, not a one-shot reveal that ends
    // with the label parked off-screen.
    expect(track).not.toBeNull()
    expect(track?.className).toMatch(/marquee-track/)
    expect(track?.style.animationDuration).toMatch(/^\d+ms$/)
  })

  it('rides two identical copies, which is what keeps the box from ever going blank', () => {
    // The load-bearing invariant. The track animates by exactly -50%, so
    // the halves must be identical for the second copy to land precisely
    // where the first began. If these two ever differ in text or in
    // trailing gap, the restart becomes a visible jump and a blank
    // stretch reappears -- the exact bug this replaced.
    const { outer, inner, container } = renderScrollingText(LONG)
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    const copies = Array.from(getTrack(container)?.children ?? []) as HTMLElement[]
    expect(copies).toHaveLength(2)
    expect(copies[0].textContent).toBe(LONG)
    expect(copies[1].textContent).toBe(LONG)
    expect(copies[0].style.paddingRight).toBe(copies[1].style.paddingRight)
    expect(copies[0].style.paddingRight).not.toBe('')
  })

  it('hides the duplicate copy from assistive tech, so the label is announced once', () => {
    const { outer, inner, container } = renderScrollingText(LONG)
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    const copies = Array.from(getTrack(container)?.children ?? []) as HTMLElement[]
    expect(copies[0]).not.toHaveAttribute('aria-hidden')
    expect(copies[1]).toHaveAttribute('aria-hidden', 'true')
  })

  it('stops looping and restores the single clipped copy the moment the pointer leaves', () => {
    const { outer, inner, container } = renderScrollingText(LONG)
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.mouseEnter(outer)
    expect(getTrack(container)).not.toBeNull()

    fireEvent.mouseLeave(outer)
    expect(getTrack(container)).toBeNull()
    const resting = outer.querySelector('span') as HTMLElement
    expect(resting.style.textOverflow).toBe('ellipsis')
  })

  it('also loops on focus and stops on blur, for keyboard users', () => {
    const { outer, inner, container } = renderScrollingText(LONG)
    mockOverflow(outer, inner, { scrollWidth: 300, clientWidth: 120 })

    fireEvent.focus(outer)
    expect(getTrack(container)).not.toBeNull()

    fireEvent.blur(outer)
    expect(getTrack(container)).toBeNull()
  })

  it('scales the loop duration with how much text there is to read', () => {
    const durationMs = (track: HTMLElement | null) =>
      Number(/(\d+)ms/.exec(track?.style.animationDuration ?? '')?.[1])

    const { outer, inner, container } = renderScrollingText(LONG)
    mockOverflow(outer, inner, { scrollWidth: 1000, clientWidth: 120 })
    fireEvent.mouseEnter(outer)
    const longDuration = durationMs(getTrack(container))

    fireEvent.mouseLeave(outer)
    const resting = outer.querySelector('span') as HTMLElement
    mockOverflow(outer, resting, { scrollWidth: 200, clientWidth: 120 })
    fireEvent.mouseEnter(outer)
    const shortDuration = durationMs(getTrack(container))

    expect(longDuration).toBeGreaterThan(shortDuration)
  })

  it('clips the text at rest via overflow-hidden on the wrapping box', () => {
    const { outer } = renderScrollingText('Anything')
    expect(outer.className).toMatch(/overflow-hidden/)
  })

  it('ellipsis-truncates at rest, so a clipped label never cuts mid-character', () => {
    const { outer, inner } = renderScrollingText(LONG)
    mockOverflow(outer, inner, { scrollWidth: 600, clientWidth: 200 })

    expect(inner.style.textOverflow).toBe('ellipsis')
    expect(inner.style.overflow).toBe('hidden')
    // The cap is what makes `text-overflow` apply at all -- an inline-block
    // at its own intrinsic width never overflows itself, so it would have
    // nothing to put an ellipsis on. The wrapper's `overflow-hidden` clips
    // this span but cannot give it an ellipsis.
    expect(inner.style.maxWidth).toBe('100%')
  })
})
