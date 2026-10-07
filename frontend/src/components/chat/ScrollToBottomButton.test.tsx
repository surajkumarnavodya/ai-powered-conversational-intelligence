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
})
