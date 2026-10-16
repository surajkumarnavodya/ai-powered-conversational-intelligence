import { createEvent, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExpandableText } from './expandable-text'

/** A clipboard the test can read back -- `fireEvent`'s ClipboardEvent has
 * a null `clipboardData` in jsdom, so the handler is given this stand-in
 * and the test asserts on what it was handed. */
function fakeClipboardData() {
  const written: Record<string, string> = {}
  return {
    written,
    setData: (type: string, value: string) => {
      written[type] = value
    },
  }
}

/** Stands in for a real user selection: jsdom has no layout and no live
 * selection, so the range is built from the rendered nodes and
 * `toString()` returns what the browser would have produced for it
 * (including any trailing newline the block boundary adds). */
function mockSelection(node: Node, text: string) {
  const range = document.createRange()
  range.selectNodeContents(node)
  vi.spyOn(window, 'getSelection').mockReturnValue({
    rangeCount: 1,
    getRangeAt: () => range,
    toString: () => text,
  } as unknown as Selection)
  return range
}

function copyFrom(target: HTMLElement) {
  const clipboardData = fakeClipboardData()
  const event = createEvent.copy(target, { clipboardData })
  fireEvent(target, event)
  return { clipboardData, event }
}

describe('ExpandableText -- copying the text out', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('copies the selected characters with no trailing blank line, in both flavors', () => {
    const question = 'Top colleges in India for executive education short courses.'
    render(<ExpandableText text={question} fadeToColor="var(--accent)" />)

    // The browser's own serialization of a whole-paragraph selection ends
    // with the block's newline -- that is the stray blank line on paste.
    const paragraph = screen.getAllByText(question)[0]
    mockSelection(paragraph, `${question}\n\n`)
    const { clipboardData, event } = copyFrom(paragraph)

    expect(clipboardData.written['text/plain']).toBe(question)
    expect(clipboardData.written['text/html']).toBe(`<span>${question}</span>`)
    expect(event.defaultPrevented).toBe(true)
  })

  it('escapes HTML and keeps interior line breaks inline', () => {
    const text = 'Rows where qty < 5 & name > "a"\nsecond line'
    render(<ExpandableText text={text} fadeToColor="var(--card)" />)

    const paragraph = screen.getAllByText(/Rows where qty/)[0]
    mockSelection(paragraph, text)
    const { clipboardData } = copyFrom(paragraph)

    expect(clipboardData.written['text/plain']).toBe(text)
    expect(clipboardData.written['text/html']).toBe(
      '<span>Rows where qty &lt; 5 &amp; name &gt; "a"<br>second line</span>',
    )
  })

  it('leaves a selection that runs outside the component to the browser', () => {
    const question = 'How many orders shipped late last quarter?'
    render(
      <div>
        <ExpandableText text={question} fadeToColor="var(--accent)" />
        <p>An answer paragraph that the selection also covers.</p>
      </div>,
    )

    // commonAncestorContainer is the wrapper, which is outside the
    // component's own root -- rewriting the clipboard here would throw
    // away the rest of what the user highlighted.
    const outer = screen.getByText(/An answer paragraph/).parentElement as HTMLElement
    mockSelection(outer, 'everything the user dragged across')
    const { clipboardData, event } = copyFrom(screen.getAllByText(question)[0])

    expect(clipboardData.written).toEqual({})
    expect(event.defaultPrevented).toBe(false)
  })

  it('does not hijack a copy when nothing is selected', () => {
    const question = 'Which regions grew fastest?'
    render(<ExpandableText text={question} fadeToColor="var(--accent)" />)

    const paragraph = screen.getAllByText(question)[0]
    mockSelection(paragraph, '   ')
    const { clipboardData, event } = copyFrom(paragraph)

    expect(clipboardData.written).toEqual({})
    expect(event.defaultPrevented).toBe(false)
  })
})
