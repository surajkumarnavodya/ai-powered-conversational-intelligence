import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { Citation, SourceAnswer } from '@/lib/types'
import { SourceAnswerCard } from './SourceAnswerCard'

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    filename: 'https://www.lemonde.fr/a',
    title: 'Le Monde report',
    chunk_index: 0,
    page_number: null,
    document_id: '',
    has_pdf_bytes: false,
    ...overrides,
  }
}

function result(overrides: Partial<SourceAnswer> = {}): SourceAnswer {
  return {
    answer: 'Protests spread to five cities ([Le Monde](https://www.lemonde.fr/a)).',
    citations: [citation()],
    status: 'succeeded',
    ...overrides,
  }
}

function renderCard(overrides: Partial<SourceAnswer> = {}) {
  return render(<SourceAnswerCard result={result(overrides)} />)
}

describe('SourceAnswerCard -- answer first', () => {
  it('renders the answer itself', () => {
    renderCard()
    expect(screen.getByText(/Protests spread to five cities/)).toBeInTheDocument()
  })

  it('replaces a long publisher link with a compact numbered marker', () => {
    renderCard()
    const link = screen.getByRole('link', { name: '[1]' })
    expect(link).toHaveAttribute('href', 'https://www.lemonde.fr/a')
    // The publisher name no longer sits mid-sentence.
    expect(screen.queryByRole('link', { name: 'Le Monde' })).not.toBeInTheDocument()
  })

  it('does not print a source-type heading, which the turn already states elsewhere', () => {
    const { container } = renderCard()
    expect(within(container).queryByText(/^Web$/)).not.toBeInTheDocument()
    expect(within(container).queryByText(/^Documents$/)).not.toBeInTheDocument()
  })
})

describe('SourceAnswerCard -- one canonical source list', () => {
  it('offers a single collapsed Sources section', () => {
    renderCard({
      citations: [citation(), citation({ filename: 'https://www.reuters.com/b', title: 'Reuters' })],
    })
    expect(screen.getByText('Sources (2)')).toBeInTheDocument()
  })

  it('counts each unique page once despite tracking-parameter duplicates', () => {
    renderCard({
      citations: [
        citation(),
        citation({ filename: 'https://www.lemonde.fr/a?utm_source=newsletter' }),
        citation({ filename: 'https://www.reuters.com/b', title: 'Reuters' }),
      ],
    })
    expect(screen.getByText('Sources (2)')).toBeInTheDocument()
    expect(screen.getByText('2 sources')).toBeInTheDocument()
  })

  it('shows each source by its real title, linked', async () => {
    renderCard()
    await userEvent.click(screen.getByText('Sources (1)'))
    const link = screen.getByRole('link', { name: /Le Monde report/ })
    expect(link).toHaveAttribute('href', 'https://www.lemonde.fr/a')
  })

  it('falls back to the domain rather than inventing a title from the slug', async () => {
    renderCard({
      citations: [citation({ filename: 'https://www.lemonde.fr/2026/clashes-in-paris', title: '' })],
    })
    await userEvent.click(screen.getByText('Sources (1)'))
    expect(screen.getByRole('link', { name: /lemonde\.fr/ })).toBeInTheDocument()
    expect(screen.queryByText(/clashes in paris/i)).not.toBeInTheDocument()
  })

  it('does not repeat the source count as a separate line', () => {
    const { container } = renderCard()
    // The old card printed "Sources: 1" under the evidence disclosure,
    // naming the same pages the disclosure already listed.
    expect(container.textContent).not.toMatch(/Sources:\s*\d/)
  })
})

describe('SourceAnswerCard -- compact evidence indicator', () => {
  it('states the evidence status and source count on one line', () => {
    renderCard()
    expect(screen.getByText('Source-backed')).toBeInTheDocument()
    expect(screen.getByText('1 source')).toBeInTheDocument()
    expect(screen.getByText('Show evidence')).toBeInTheDocument()
  })

  it('keeps the evidence detail collapsed until asked for', () => {
    renderCard()
    const toggle = screen.getByRole('button', { name: /show evidence/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/refer to these sources/i)).not.toBeInTheDocument()
  })

  it('opens the evidence detail on demand', async () => {
    renderCard()
    await userEvent.click(screen.getByRole('button', { name: /show evidence/i }))
    expect(screen.getByRole('button', { name: /hide evidence/i })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(screen.getByText(/refer to these sources/i)).toBeInTheDocument()
  })

  it('drops the standing explanation sentence the old panel always showed', () => {
    const { container } = renderCard()
    expect(container.textContent).not.toMatch(/Taken from the cited source documents/i)
    expect(container.textContent).not.toMatch(/Backed by \d+ cited source/i)
  })

  it('says "Not available", never "Source-backed", when nothing is cited', () => {
    // A policy answer whose material was withheld, or the
    // insufficient-information fallback. Claiming a source here would
    // turn an access control into a false endorsement.
    renderCard({ answer: 'No supporting material is available.', citations: [] })
    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(screen.queryByText('Source-backed')).not.toBeInTheDocument()
    expect(screen.queryByText(/^Sources \(/)).not.toBeInTheDocument()
  })
})
