import { afterEach, describe, expect, it, vi } from 'vitest'
import { answerFilenameStem, buildMarkdownDocument, downloadTextFile } from './markdownFile'

describe('buildMarkdownDocument', () => {
  it('puts the question in an H1 above the answer', () => {
    expect(buildMarkdownDocument('Top 5 regions?', '## Answer\n\nNorth leads.')).toBe(
      '# Top 5 regions?\n\n## Answer\n\nNorth leads.\n',
    )
  })

  it('trims both parts so stray whitespace cannot break the heading', () => {
    expect(buildMarkdownDocument('  Revenue?  ', '\n\nUp 4%.\n\n')).toBe('# Revenue?\n\nUp 4%.\n')
  })

  it('omits the heading entirely when there is no question', () => {
    // Rather than emitting a bare "# " line, which renders as an empty
    // heading in every markdown viewer.
    expect(buildMarkdownDocument('   ', 'Just the answer.')).toBe('Just the answer.\n')
  })

  it('ends with exactly one newline', () => {
    const doc = buildMarkdownDocument('Q', 'A')
    expect(doc.endsWith('\n')).toBe(true)
    expect(doc.endsWith('\n\n')).toBe(false)
  })
})

describe('answerFilenameStem', () => {
  it('slugifies the question', () => {
    expect(answerFilenameStem('Top 5 regions by revenue')).toBe('Top-5-regions-by-revenue')
  })

  it('drops characters a filesystem would reject', () => {
    expect(answerFilenameStem('What/is\\the "net" revenue?')).toBe('Whatisthe-net-revenue')
  })

  it('caps the length so a long question cannot produce an unusable name', () => {
    expect(answerFilenameStem('x'.repeat(200)).length).toBeLessThanOrEqual(60)
  })

  it('falls back to "answer" when nothing usable survives', () => {
    expect(answerFilenameStem('???')).toBe('answer')
    expect(answerFilenameStem('')).toBe('answer')
  })
})

describe('downloadTextFile', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('names the download and revokes the object URL', () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:fake')
    const revokeObjectURL = vi.fn()
    Object.assign(URL, { createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    downloadTextFile('report.md', '# Hi\n', 'text/markdown;charset=utf-8')

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake')
  })

  it('still revokes the object URL when the click throws', () => {
    const revokeObjectURL = vi.fn()
    Object.assign(URL, {
      createObjectURL: vi.fn().mockReturnValue('blob:fake'),
      revokeObjectURL,
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      // A browser that blocks a programmatic download, for instance.
      throw new Error('blocked')
    })

    expect(() => downloadTextFile('report.md', '# Hi\n', 'text/markdown')).toThrow('blocked')
    // The `finally` is the point of this test: without it a blocked
    // download leaks the blob for the lifetime of the document.
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake')
  })
})
