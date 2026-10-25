import { describe, expect, it } from 'vitest'
import {
  canonicalSourceKey,
  sourceDomain,
  sourceLabel,
  sourceNumbers,
  uniqueSources,
} from './citations'
import type { Citation } from './types'

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    filename: 'https://example.com/article',
    chunk_index: 0,
    page_number: null,
    document_id: '',
    has_pdf_bytes: false,
    ...overrides,
  }
}

describe('canonicalSourceKey', () => {
  it('treats http and https as the same page', () => {
    expect(canonicalSourceKey(citation({ filename: 'http://example.com/a' }))).toBe(
      canonicalSourceKey(citation({ filename: 'https://example.com/a' })),
    )
  })

  it('ignores a leading www. and host casing', () => {
    expect(canonicalSourceKey(citation({ filename: 'https://WWW.Example.com/a' }))).toBe(
      canonicalSourceKey(citation({ filename: 'https://example.com/a' })),
    )
  })

  it('ignores a fragment, which is a position inside one document', () => {
    expect(canonicalSourceKey(citation({ filename: 'https://example.com/a#part-2' }))).toBe(
      canonicalSourceKey(citation({ filename: 'https://example.com/a' })),
    )
  })

  it('ignores a trailing slash on an otherwise identical path', () => {
    expect(canonicalSourceKey(citation({ filename: 'https://example.com/a/' }))).toBe(
      canonicalSourceKey(citation({ filename: 'https://example.com/a' })),
    )
  })

  it('strips campaign parameters, including odd casing', () => {
    expect(
      canonicalSourceKey(citation({ filename: 'https://example.com/a?utm_source=x&UTM_Medium=y' })),
    ).toBe(canonicalSourceKey(citation({ filename: 'https://example.com/a' })))
    expect(canonicalSourceKey(citation({ filename: 'https://example.com/a?fbclid=z' }))).toBe(
      canonicalSourceKey(citation({ filename: 'https://example.com/a' })),
    )
  })

  it('orders surviving parameters so argument order does not create a duplicate', () => {
    expect(canonicalSourceKey(citation({ filename: 'https://example.com/a?b=2&a=1' }))).toBe(
      canonicalSourceKey(citation({ filename: 'https://example.com/a?a=1&b=2' })),
    )
  })

  describe('never merges genuinely different pages', () => {
    // The dangerous direction: collapsing two articles into one citation
    // attributes a claim to a page that never supported it.
    it.each([
      ['https://example.com/a?id=1', 'https://example.com/a?id=2'],
      ['https://example.com/a?v=abc', 'https://example.com/a?v=xyz'],
      ['https://example.com/a', 'https://example.com/b'],
      ['https://example.com/a', 'https://other.com/a'],
      ['https://example.com:8443/a', 'https://example.com/a'],
      ['https://example.com/a/b', 'https://example.com/a/b/c'],
      // A meaningful parameter alongside a tracking one survives.
      ['https://example.com/a?storyId=7&utm_source=x', 'https://example.com/a?storyId=8'],
    ])('%s is distinct from %s', (left, right) => {
      expect(canonicalSourceKey(citation({ filename: left }))).not.toBe(
        canonicalSourceKey(citation({ filename: right })),
      )
    })
  })

  it('falls back to the document id for a stored document, so two chunks are one source', () => {
    const a = citation({ filename: 'handbook.pdf', document_id: 'doc-1', chunk_index: 0 })
    const b = citation({ filename: 'handbook.pdf', document_id: 'doc-1', chunk_index: 5 })
    expect(canonicalSourceKey(a)).toBe(canonicalSourceKey(b))
  })

  it('keeps two different documents apart even when the filename matches', () => {
    const a = citation({ filename: 'handbook.pdf', document_id: 'doc-1' })
    const b = citation({ filename: 'handbook.pdf', document_id: 'doc-2' })
    expect(canonicalSourceKey(a)).not.toBe(canonicalSourceKey(b))
  })
})

describe('sourceLabel', () => {
  it('prefers the producer-supplied title', () => {
    expect(sourceLabel(citation({ title: 'Clashes break out in Paris' }))).toBe(
      'Clashes break out in Paris',
    )
  })

  it('falls back to the domain rather than inventing a headline from the slug', () => {
    // Deriving "Clashes Break Out In Paris" from the path would be a
    // title no source actually carries.
    const value = sourceLabel(
      citation({ filename: 'https://www.lemonde.fr/2026/10/25/clashes-break-out-in-paris' }),
    )
    expect(value).toBe('lemonde.fr')
    expect(value).not.toMatch(/clashes/i)
  })

  it('ignores a whitespace-only title', () => {
    expect(sourceLabel(citation({ title: '   ' }))).toBe('example.com')
  })

  it('uses the filename when there is no title and no domain', () => {
    expect(sourceLabel(citation({ filename: 'handbook.pdf', document_id: 'doc-1' }))).toBe(
      'handbook.pdf',
    )
  })
})

describe('sourceDomain', () => {
  it('returns the bare host for a URL', () => {
    expect(sourceDomain(citation({ filename: 'https://www.bbc.co.uk/news/x' }))).toBe('bbc.co.uk')
  })

  it('returns null for a stored document, rather than a placeholder', () => {
    expect(sourceDomain(citation({ filename: 'handbook.pdf' }))).toBeNull()
  })
})

describe('uniqueSources', () => {
  it('keeps first-seen order', () => {
    const list = uniqueSources([
      citation({ filename: 'https://a.com/1' }),
      citation({ filename: 'https://b.com/2' }),
    ])
    expect(list.map((c) => c.filename)).toEqual(['https://a.com/1', 'https://b.com/2'])
  })

  it('collapses tracking-only variants of one page', () => {
    const list = uniqueSources([
      citation({ filename: 'https://a.com/1' }),
      citation({ filename: 'https://a.com/1?utm_source=newsletter' }),
      citation({ filename: 'http://www.a.com/1#top' }),
    ])
    expect(list).toHaveLength(1)
  })

  it('prefers the duplicate that carries a real title', () => {
    const list = uniqueSources([
      citation({ filename: 'https://a.com/1' }),
      citation({ filename: 'https://a.com/1?utm_source=x', title: 'The real headline' }),
    ])
    expect(list).toHaveLength(1)
    expect(list[0].title).toBe('The real headline')
  })

  it('returns an empty list for no citations', () => {
    expect(uniqueSources([])).toEqual([])
  })
})

describe('sourceNumbers', () => {
  it('numbers from 1 in the order the sources are listed', () => {
    const citations = [citation({ filename: 'https://a.com/1' }), citation({ filename: 'https://b.com/2' })]
    const numbers = sourceNumbers(citations)
    expect(numbers.get(canonicalSourceKey(citations[0]))).toBe(1)
    expect(numbers.get(canonicalSourceKey(citations[1]))).toBe(2)
  })

  it('gives two variants of one page the same number', () => {
    const citations = [
      citation({ filename: 'https://a.com/1' }),
      citation({ filename: 'https://a.com/1?utm_source=x' }),
      citation({ filename: 'https://b.com/2' }),
    ]
    const numbers = sourceNumbers(citations)
    expect(numbers.get(canonicalSourceKey(citations[1]))).toBe(1)
    expect(numbers.get(canonicalSourceKey(citations[2]))).toBe(2)
  })
})
