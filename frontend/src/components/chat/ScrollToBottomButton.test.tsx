import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ScrollToBottomButton } from './ScrollToBottomButton'

describe('ScrollToBottomButton', () => {
  it('is hidden (aria-hidden, not tab-reachable) when not visible, but stays mounted for its fade transition', () => {
    render(<ScrollToBottomButton visible={false} onClick={vi.fn()} bottomOffsetPx={16} />)
    const button = screen.getByRole('button', { hidden: true })
    expect(button).toHaveAttribute('aria-hidden', 'true')
    expect(button).toHaveAttribute('tabindex', '-1')
    expect(button.className).toMatch(/opacity-0/)
  })

  it('is accessible and focusable once visible', () => {
    render(<ScrollToBottomButton visible={true} onClick={vi.fn()} bottomOffsetPx={16} />)
    const button = screen.getByRole('button', { name: 'Scroll to latest message' })
    expect(button).toHaveAttribute('aria-hidden', 'false')
    expect(button).toHaveAttribute('tabindex', '0')
    expect(button).toHaveAttribute('title', 'Scroll to latest message')
    expect(button.className).toMatch(/opacity-100/)
  })

  it('calls onClick when clicked', async () => {
    const onClick = vi.fn()
    render(<ScrollToBottomButton visible={true} onClick={onClick} bottomOffsetPx={16} />)
    await userEvent.click(screen.getByRole('button', { name: 'Scroll to latest message' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('is keyboard-activatable (Enter) once focused', async () => {
    const onClick = vi.fn()
    render(<ScrollToBottomButton visible={true} onClick={onClick} bottomOffsetPx={16} />)
    const button = screen.getByRole('button', { name: 'Scroll to latest message' })
    button.focus()
    await userEvent.keyboard('{Enter}')
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('positions itself using the given bottom offset, so it never covers the composer', () => {
    render(<ScrollToBottomButton visible={true} onClick={vi.fn()} bottomOffsetPx={92} />)
    const button = screen.getByRole('button', { name: 'Scroll to latest message' })
    expect(button.style.bottom).toBe('92px')
  })

  describe('processing state (the dotted progress ring)', () => {
    it('shows no ring and no busy state when idle', () => {
      render(<ScrollToBottomButton visible={true} onClick={vi.fn()} bottomOffsetPx={16} />)
      const button = screen.getByRole('button', { name: 'Scroll to latest message' })
      expect(button).not.toHaveAttribute('aria-busy')
      expect(button.className).not.toMatch(/dotted-ring/)
      expect(button).not.toHaveAttribute('aria-describedby')
    })

    it('draws the ring and marks itself busy while processing', () => {
      render(
        <ScrollToBottomButton visible={true} processing onClick={vi.fn()} bottomOffsetPx={16} />,
      )
      const button = screen.getByRole('button', { name: 'Scroll to latest message' })
      expect(button).toHaveAttribute('aria-busy', 'true')
      expect(button.className).toMatch(/dotted-ring/)
    })

    it('keeps its accessible name describing the action, not the state', () => {
      // A control named "Processing…" would not tell a screen-reader user
      // what activating it does. State belongs in aria-busy/description.
      render(
        <ScrollToBottomButton
          visible={true}
          processing
          stageLabel="Schema"
          onClick={vi.fn()}
          bottomOffsetPx={16}
        />,
      )
      expect(screen.getByRole('button', { name: 'Scroll to latest message' })).toBeInTheDocument()
    })

    it('still scrolls while processing -- the ring is an additional state, not a disable', async () => {
      const onClick = vi.fn()
      render(<ScrollToBottomButton visible={true} processing onClick={onClick} bottomOffsetPx={16} />)
      const button = screen.getByRole('button', { name: 'Scroll to latest message' })
      expect(button).not.toBeDisabled()
      await userEvent.click(button)
      expect(onClick).toHaveBeenCalledTimes(1)
      button.focus()
      await userEvent.keyboard('{Enter}')
      expect(onClick).toHaveBeenCalledTimes(2)
    })

    it('names the running stage when the server reported one', () => {
      render(
        <ScrollToBottomButton
          visible={true}
          processing
          stageLabel="Execution"
          onClick={vi.fn()}
          bottomOffsetPx={16}
        />,
      )
      const button = screen.getByRole('button', { name: 'Scroll to latest message' })
      expect(button).toHaveAttribute('title', 'Scroll to latest message — Processing — Execution')
      const describedBy = button.getAttribute('aria-describedby')
      expect(describedBy).toBeTruthy()
      expect(document.getElementById(describedBy as string)?.textContent).toBe(
        'Processing — Execution',
      )
    })

    it('falls back to a plain "Processing…" rather than inventing a stage', () => {
      // `stage` comes from /ask/stream progress events; when streaming is
      // unavailable there is genuinely no stage to name.
      render(
        <ScrollToBottomButton
          visible={true}
          processing
          stageLabel={null}
          onClick={vi.fn()}
          bottomOffsetPx={16}
        />,
      )
      const button = screen.getByRole('button', { name: 'Scroll to latest message' })
      expect(button).toHaveAttribute('title', 'Scroll to latest message — Processing…')
      const describedBy = button.getAttribute('aria-describedby')
      expect(document.getElementById(describedBy as string)?.textContent).toBe('Processing…')
    })

    it('does not add a second live region -- ChatLiveRegion owns announcements', () => {
      const { container } = render(
        <ScrollToBottomButton
          visible={true}
          processing
          stageLabel="Schema"
          onClick={vi.fn()}
          bottomOffsetPx={16}
        />,
      )
      expect(container.querySelector('[aria-live]')).toBeNull()
      expect(container.querySelector('[role="status"]')).toBeNull()
    })

    it('drops the ring again the moment processing ends', () => {
      const { rerender } = render(
        <ScrollToBottomButton visible={true} processing onClick={vi.fn()} bottomOffsetPx={16} />,
      )
      expect(
        screen.getByRole('button', { name: 'Scroll to latest message' }).className,
      ).toMatch(/dotted-ring/)

      rerender(
        <ScrollToBottomButton
          visible={true}
          processing={false}
          onClick={vi.fn()}
          bottomOffsetPx={16}
        />,
      )
      const button = screen.getByRole('button', { name: 'Scroll to latest message' })
      expect(button.className).not.toMatch(/dotted-ring/)
      expect(button).not.toHaveAttribute('aria-busy')
    })

    it('keeps the same 32px box and offset while processing, so nothing shifts', () => {
      const idle = render(
        <ScrollToBottomButton visible={true} onClick={vi.fn()} bottomOffsetPx={92} />,
      )
      const idleClass = screen
        .getByRole('button', { name: 'Scroll to latest message' })
        .className.replace(/\s+/g, ' ')
      idle.unmount()

      render(<ScrollToBottomButton visible={true} processing onClick={vi.fn()} bottomOffsetPx={92} />)
      const busy = screen.getByRole('button', { name: 'Scroll to latest message' })
      // Identical box/position utilities; the ring is a pseudo-element.
      expect(busy.className).toMatch(/h-8 w-8/)
      // No invisible hit-area expansion: the transparent 44px box this
      // briefly carried floated over the conversation and swallowed
      // clicks and text selection around a control the reader could not
      // see. 32px already clears WCAG 2.2's 24px minimum.
      expect(busy.className).not.toMatch(/hit-area/)
      expect(busy.style.bottom).toBe('92px')
      expect(busy.className.replace(/\s+/g, ' ').replace(' dotted-ring', '')).toBe(idleClass)
    })
  })
})
