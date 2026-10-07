import { describe, expect, it } from 'vitest'
import { shortenQuestion } from './questionLabel'

describe('shortenQuestion', () => {
  it('returns a short question unchanged', () => {
    expect(shortenQuestion('Show revenue by region')).toBe('Show revenue by region')
  })

  it('truncates a long question at a word boundary with an ellipsis', () => {
    const full =
      'Can you show me the top five companies by annual revenue in India and compare them with last year?'
    const result = shortenQuestion(full)

    expect(result.endsWith('…')).toBe(true)
    expect(result.length).toBeLessThan(full.length)
    // Never cuts mid-word: the character right before the ellipsis is not
    // itself a fragment of the next word in the original text.
    expect(full.startsWith(result.slice(0, -1).trimEnd())).toBe(true)
  })

  it('never produces a label longer than the requested limit plus the ellipsis character', () => {
    const full = 'word '.repeat(40).trim()
    const result = shortenQuestion(full, 20)

    expect(result.length).toBeLessThanOrEqual(21)
  })

  it('collapses internal whitespace and newlines into a single line', () => {
    const result = shortenQuestion('Show   revenue\n\nby region')
    expect(result).toBe('Show revenue by region')
  })

  it('falls back to a hard cut for one very long unbroken token', () => {
    const full = 'a'.repeat(60)
    const result = shortenQuestion(full, 20)

    expect(result).toBe(`${'a'.repeat(20)}…`)
  })

  it('does not mutate or depend on any shared state across calls', () => {
    const a = shortenQuestion('First question here that is long enough to truncate for sure')
    const b = shortenQuestion('Second question here that is also long enough to truncate for sure')

    expect(a).not.toBe(b)
  })
})
