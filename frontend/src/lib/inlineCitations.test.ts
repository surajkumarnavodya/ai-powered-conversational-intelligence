import { describe, expect, it } from 'vitest'
import { hasOwnNumericMarkers, withNumberedCitations } from './inlineCitations'
import type { Citation } from './types'

function citation(filename: string, title = ''): Citation {
  return { filename, title, chunk_index: 0, page_number: null, document_id: '', has_pdf_bytes: false }
}

const SOURCES = [
  citation('https://www.lemonde.fr/a', 'Le Monde report'),
  citation('https://www.reuters.com/b', 'Reuters report'),
]

describe('withNumberedCitations', () => {
  it('rewrites a publisher link into the compact marker for that source', () => {
    const { text } = withNumberedCitations(
      'Protests spread ([Le Monde](https://www.lemonde.fr/a)).',
      SOURCES,
    )
    expect(text).toBe('Protests spread ([[1]](https://www.lemonde.fr/a)).')
  })

  it('numbers by position in the canonical source list', () => {
    const { text } = withNumberedCitations(
      'A ([Le Monde](https://www.lemonde.fr/a)) and B ([Reuters](https://www.reuters.com/b)).',
      SOURCES,
    )
    expect(text).toContain('[[1]](https://www.lemonde.fr/a)')
    expect(text).toContain('[[2]](https://www.reuters.com/b)')
  })

  it('gives two links to the same page the same number', () => {
    const { text, citedNumbers } = withNumberedCitations(
      'A ([X](https://www.lemonde.fr/a)) and again ([Y](https://lemonde.fr/a?utm_source=z)).',
      SOURCES,
    )
    expect(text.match(/\[\[1\]\]/g)).toHaveLength(2)
    expect(citedNumbers).toEqual([1])
  })

  it('reports which sources the prose actually leans on', () => {
    const { citedNumbers } = withNumberedCitations(
      'Only one is cited ([Reuters](https://www.reuters.com/b)).',
      SOURCES,
    )
    expect(citedNumbers).toEqual([2])
  })

  describe('never fabricates a citation', () => {
    it('leaves a link to a page that is not cited exactly as written', () => {
      const answer = 'See also ([Some Blog](https://uncited.example.com/post)).'
      expect(withNumberedCitations(answer, SOURCES).text).toBe(answer)
    })

    it('does not number anything when the answer has no citations at all', () => {
      const answer = 'Unsupported ([Le Monde](https://www.lemonde.fr/a)).'
      expect(withNumberedCitations(answer, []).text).toBe(answer)
    })

    it('leaves images alone', () => {
      const answer = '![a chart](https://www.lemonde.fr/a)'
      expect(withNumberedCitations(answer, SOURCES).text).toBe(answer)
    })
  })

  it('is idempotent, so a re-rendered saved turn is not renumbered', () => {
    const once = withNumberedCitations('A ([Le Monde](https://www.lemonde.fr/a)).', SOURCES).text
    expect(withNumberedCitations(once, SOURCES).text).toBe(once)
  })

  it('returns an empty answer unchanged', () => {
    expect(withNumberedCitations('', SOURCES).text).toBe('')
  })

  it('leaves surrounding prose and markdown structure intact', () => {
    const answer = '## Politics\n\n- **Modi** leads ([Reuters](https://www.reuters.com/b))\n'
    const { text } = withNumberedCitations(answer, SOURCES)
    expect(text).toBe('## Politics\n\n- **Modi** leads ([[2]](https://www.reuters.com/b))\n')
  })
})

describe('hasOwnNumericMarkers', () => {
  it('detects markers the model wrote itself', () => {
    expect(hasOwnNumericMarkers('Modi leads [1] and inflation fell [2].')).toBe(true)
  })

  it('does not mistake a markdown link label for a bare marker', () => {
    expect(hasOwnNumericMarkers('See ([Le Monde](https://www.lemonde.fr/a)).')).toBe(false)
  })

  it('does not mistake a marker this module produced for the model’s own', () => {
    // `[[1]](url)` is a link whose label is `[1]` -- already handled.
    expect(hasOwnNumericMarkers('See ([[1]](https://www.lemonde.fr/a)).')).toBe(false)
  })

  it('is false for ordinary prose', () => {
    expect(hasOwnNumericMarkers('Revenue grew 12% in 2025.')).toBe(false)
  })
})
