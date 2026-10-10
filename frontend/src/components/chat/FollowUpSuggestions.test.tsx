import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Recommendation } from '@/lib/types'
import { FollowUpSuggestions, selectFollowUpQuestions } from './FollowUpSuggestions'

function recommendation(
  claim: string,
  kind: 'next_question' | 'action' = 'next_question',
): Recommendation {
  return {
    kind,
    claim: { value: claim, level: 'ai_inference', grounded_in: [], source: null },
    rationale: null,
    category: null,
    evidence: [],
    affected_entity: null,
    action: null,
    measurable_impact: null,
    confidence: null,
    rule_or_model: null,
    limitations: [],
    engine_version: '1.0.0',
    generated_at: '2026-10-10T00:00:00Z',
  } as unknown as Recommendation
}

const NEWLINE = String.fromCharCode(10)
const BELL = String.fromCharCode(7)

describe('selectFollowUpQuestions', () => {
  it('keeps only next_question recommendations', () => {
    const picked = selectFollowUpQuestions([
      recommendation('Revenue by region?'),
      recommendation('Add an index on OrderDate', 'action'),
    ])
    expect(picked).toEqual(['Revenue by region?'])
  })

  it('deduplicates the same question from two rules', () => {
    // Two rules can legitimately produce the same question; showing it
    // twice looks like a bug.
    const picked = selectFollowUpQuestions([
      recommendation('Revenue by region?'),
      recommendation('revenue by region?'),
    ])
    expect(picked).toHaveLength(1)
  })

  it('caps the list so it stays a suggestion, not a second nav surface', () => {
    const many = Array.from({ length: 9 }, (_, index) => recommendation(`Question ${index}?`))
    expect(selectFollowUpQuestions(many)).toHaveLength(4)
  })

  it('drops an empty or whitespace-only question', () => {
    expect(selectFollowUpQuestions([recommendation('   ')])).toEqual([])
  })

  it('normalizes a database-derived label', () => {
    // `sanitizeLabel` replaces control characters, collapses whitespace
    // and caps the length. It does not strip markup, and does not need
    // to: React escapes on render, and a clicked chip goes back through
    // the normal /ask path, so the input guard, the governance request
    // policy and the SQL validator all apply to it. What this prevents
    // is a newline-spliced instruction or an unbounded label.
    const raw = `Revenue   by${NEWLINE}${NEWLINE}region${BELL} ?`
    const picked = selectFollowUpQuestions([recommendation(raw)])
    expect(picked[0]).toBe('Revenue by region ?')
  })

  it('caps an unbounded label', () => {
    const picked = selectFollowUpQuestions([recommendation('x'.repeat(500))])
    expect(picked[0].length).toBeLessThanOrEqual(200)
  })

  it('returns nothing for no recommendations', () => {
    expect(selectFollowUpQuestions([])).toEqual([])
  })
})

describe('FollowUpSuggestions', () => {
  it('renders nothing when there are no follow-up questions', () => {
    const { container } = render(
      <FollowUpSuggestions recommendations={[recommendation('Do a thing', 'action')]} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders one chip per question under a labelled list', () => {
    render(
      <FollowUpSuggestions
        recommendations={[recommendation('Revenue by region?'), recommendation('Top products?')]}
      />,
    )
    const list = screen.getByRole('list', { name: 'Ask next' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
  })

  it('asks the question when a chip is clicked', () => {
    const onAsk = vi.fn()
    render(
      <FollowUpSuggestions recommendations={[recommendation('Revenue by region?')]} onAsk={onAsk} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Revenue by region?' }))
    expect(onAsk).toHaveBeenCalledWith('Revenue by region?')
  })

  it('passes the normalized text to onAsk, not the raw label', () => {
    const onAsk = vi.fn()
    render(
      <FollowUpSuggestions
        recommendations={[recommendation(`Revenue  for${NEWLINE}EMEA?`)]}
        onAsk={onAsk}
      />,
    )
    fireEvent.click(screen.getAllByRole('button')[0])
    expect(onAsk).toHaveBeenCalledWith('Revenue for EMEA?')
  })

  it('renders static chips when no handler is supplied', () => {
    // A reloaded past turn has no live ask handler.
    render(<FollowUpSuggestions recommendations={[recommendation('Revenue by region?')]} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('Revenue by region?')).toBeInTheDocument()
  })

  it('disables the chips while another question is in flight', () => {
    render(
      <FollowUpSuggestions
        recommendations={[recommendation('Revenue by region?')]}
        onAsk={vi.fn()}
        disabled
      />,
    )
    expect(screen.getByRole('button', { name: 'Revenue by region?' })).toBeDisabled()
  })

  it('states that the suggestions are AI estimates', () => {
    render(<FollowUpSuggestions recommendations={[recommendation('Revenue by region?')]} />)
    expect(screen.getByText(/AI estimate/i)).toBeInTheDocument()
  })
})
