import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ImageUploader } from './ImageUploader'

describe('ImageUploader -- the composer\'s "+" control', () => {
  it('names itself for assistive tech without a competing native title', () => {
    render(<ImageUploader onFilesSelected={vi.fn()} />)

    const button = screen.getByRole('button', { name: 'Attach a file' })
    // A `title` alongside the Tooltip would surface the same hint twice,
    // in two different styles, on the same hover.
    expect(button).not.toHaveAttribute('title')
  })

  it('shows the hint on keyboard focus, which a native title does not do', async () => {
    render(<ImageUploader onFilesSelected={vi.fn()} />)

    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'Attach a file' })).toHaveFocus()
    await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent('Attach a file'))
  })

  it('opens the file picker when clicked', async () => {
    render(<ImageUploader onFilesSelected={vi.fn()} />)

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const click = vi.spyOn(input, 'click').mockImplementation(() => {})
    await userEvent.click(screen.getByRole('button', { name: 'Attach a file' }))

    expect(click).toHaveBeenCalledOnce()
  })
})
