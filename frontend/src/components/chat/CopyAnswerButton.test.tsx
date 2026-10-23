import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CopyAnswerButton } from './CopyAnswerButton'

/** `copyMarkdownToClipboard` dynamically imports `react-dom/server` and
 * builds a rich-text ClipboardItem. These tests are about *this
 * component's* behaviour -- what it shows, when, and whether it ever
 * claims success it did not get -- so the clipboard module is stubbed and
 * its own rich/plain fallback is left to the browser it targets. */
vi.mock('@/lib/clipboard', () => ({
  copyMarkdownToClipboard: vi.fn(),
}))
import { copyMarkdownToClipboard } from '@/lib/clipboard'

const ANSWER = '**Insight**: West leads with 4,812 units.\n\n| region | units |\n| --- | --- |\n| West | 4812 |'

function copyButton() {
  return screen.getByRole('button', { name: /copy answer|copied/i })
}

beforeEach(() => {
  vi.mocked(copyMarkdownToClipboard).mockReset().mockResolvedValue(undefined)
  vi.useRealTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('CopyAnswerButton', () => {
  it('copies exactly the answer markdown it was given, not scraped DOM text', async () => {
    render(<CopyAnswerButton answer={ANSWER} />)

    await userEvent.click(copyButton())

    expect(copyMarkdownToClipboard).toHaveBeenCalledTimes(1)
    // Structure preserved verbatim: the table and the blank lines survive,
    // because this is the canonical markdown rather than textContent.
    expect(copyMarkdownToClipboard).toHaveBeenCalledWith(ANSWER)
  })

  it('is type="button", so it cannot submit the composer form', () => {
    render(<CopyAnswerButton answer={ANSWER} />)
    expect(copyButton()).toHaveAttribute('type', 'button')
  })

  it('confirms only after the clipboard write actually resolves', async () => {
    let resolveWrite: (() => void) | undefined
    vi.mocked(copyMarkdownToClipboard).mockReturnValue(
      new Promise<void>((resolve) => {
        resolveWrite = resolve
      }),
    )
    render(<CopyAnswerButton answer={ANSWER} />)

    await userEvent.click(copyButton())
    // Still in flight -- nothing may claim success yet.
    expect(screen.queryByRole('button', { name: /copied/i })).toBeNull()

    resolveWrite?.()
    await waitFor(() => expect(screen.getByRole('button', { name: /copied/i })).toBeInTheDocument())
  })

  it('never claims success when the clipboard write fails, and offers a fallback', async () => {
    vi.mocked(copyMarkdownToClipboard).mockRejectedValue(new Error('NotAllowedError'))
    render(<CopyAnswerButton answer={ANSWER} />)

    await userEvent.click(copyButton())

    await waitFor(() =>
      expect(screen.getByTestId('copy-answer-failed')).toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: /copied/i })).toBeNull()
    // The fallback is actionable, not just "something went wrong".
    expect(screen.getByTestId('copy-answer-failed').textContent?.toLowerCase()).toContain('select')
  })

  it('announces the outcome politely, and says nothing at rest', async () => {
    const { container } = render(<CopyAnswerButton answer={ANSWER} />)
    const status = container.querySelector('[role="status"]')

    expect(status).toHaveAttribute('aria-live', 'polite')
    // Empty at rest: nothing is announced on mount or on a re-render.
    expect(status?.textContent).toBe('')

    await userEvent.click(copyButton())
    await waitFor(() => expect(status?.textContent).toMatch(/copied/i))
  })

  it('does not announce the failure twice', async () => {
    vi.mocked(copyMarkdownToClipboard).mockRejectedValue(new Error('denied'))
    const { container } = render(<CopyAnswerButton answer={ANSWER} />)

    await userEvent.click(copyButton())

    await waitFor(() => expect(screen.getByTestId('copy-answer-failed')).toBeInTheDocument())
    // The visible copy is hidden from assistive tech; the live region is
    // the single speaker.
    expect(screen.getByTestId('copy-answer-failed')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelectorAll('[role="status"]')).toHaveLength(1)
  })

  it('does not offer copying while the response is still a placeholder', async () => {
    render(<CopyAnswerButton answer={ANSWER} disabled />)

    const button = copyButton()
    expect(button).toBeDisabled()
    await userEvent.click(button)
    expect(copyMarkdownToClipboard).not.toHaveBeenCalled()
  })

  it('keeps each response’s copied state to itself', async () => {
    render(
      <>
        <div data-testid="a">
          <CopyAnswerButton answer="answer A" />
        </div>
        <div data-testid="b">
          <CopyAnswerButton answer="answer B" />
        </div>
      </>,
    )

    const inA = screen.getByTestId('a').querySelector('button') as HTMLButtonElement
    await userEvent.click(inA)

    await waitFor(() =>
      expect(screen.getByTestId('a').querySelector('button')).toHaveAttribute(
        'aria-label',
        expect.stringMatching(/copied/i),
      ),
    )
    // The other answer must still offer a plain "Copy answer".
    expect(screen.getByTestId('b').querySelector('button')).toHaveAttribute(
      'aria-label',
      expect.stringMatching(/copy answer/i),
    )
    expect(copyMarkdownToClipboard).toHaveBeenCalledWith('answer A')
  })

  it('clears its reset timer on unmount rather than setting state on a gone component', async () => {
    const clearSpy = vi.spyOn(window, 'clearTimeout')
    const { unmount } = render(<CopyAnswerButton answer={ANSWER} />)

    await userEvent.click(copyButton())
    await waitFor(() => expect(screen.getByRole('button', { name: /copied/i })).toBeInTheDocument())
    unmount()

    expect(clearSpy).toHaveBeenCalled()
    clearSpy.mockRestore()
  })

  it('is reachable and operable from the keyboard', async () => {
    render(<CopyAnswerButton answer={ANSWER} />)
    const button = copyButton()

    button.focus()
    expect(button).toHaveFocus()
    await userEvent.keyboard('{Enter}')

    expect(copyMarkdownToClipboard).toHaveBeenCalledWith(ANSWER)
  })
})
