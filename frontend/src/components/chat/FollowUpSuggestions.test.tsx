import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Recommendation } from '@/lib/types'
import { FollowUpSuggestions, selectFollowUpQuestions } from './FollowUpSuggestions'

function recommendation(
  claim: string,
  {
    kind = 'next_question',
    action = null,
  }: { kind?: 'next_question' | 'action'; action?: string | null } = {},
): Recommendation {
  return {
    kind,
    claim: { value: claim, level: 'ai_inference', grounded_in: [], source: null },
    rationale: null,
    category: kind === 'next_question' ? 'exploration' : null,
    evidence: [],
    affected_entity: null,
    action,
    measurable_impact: null,
    confidence: null,
    rule_or_model: null,
    limitations: [],
    engine_version: '1.0.0',
    generated_at: '2026-10-10T00:00:00Z',
  } as unknown as Recommendation
}

/** A chip as `recommendation.followups` actually produces it: a short
 * `action` for the label, the full question in `claim.value`. */
function chip(label: string, question: string): Recommendation {
  return recommendation(question, { action: label })
}

const NEWLINE = String.fromCharCode(10)
const BELL = String.fromCharCode(7)

describe('selectFollowUpQuestions', () => {
  it('keeps only next_question recommendations', () => {
    const picked = selectFollowUpQuestions([
      chip('Top 5', 'Top 5 Product Category by Sales Amount'),
      recommendation('Add an index on OrderDate', { kind: 'action', action: 'Add index' }),
    ])
    expect(picked).toEqual([
      { label: 'Top 5', question: 'Top 5 Product Category by Sales Amount' },
    ])
  })

  it('uses the short action as the label and the claim as the question', () => {
    // The split is the whole point: the chip reads "By Region" while what
    // gets asked stays unambiguous.
    const picked = selectFollowUpQuestions([
      chip('By Region', 'Break down Net Revenue by Sales Region'),
    ])
    expect(picked[0].label).toBe('By Region')
    expect(picked[0].question).toBe('Break down Net Revenue by Sales Region')
  })

  it('falls back to the question as its own label when there is no action', () => {
    // `analytics.forecasting`/`analytics.scenario` emit a full sentence
    // and no short action. Those must still render, not silently vanish.
    const picked = selectFollowUpQuestions([recommendation('Ask about a longer history window')])
    expect(picked[0].question).toBe('Ask about a longer history window')
    expect(picked[0].label).toBeTruthy()
  })

  it('caps a label that is too long to scan', () => {
    const picked = selectFollowUpQuestions([
      recommendation('Ask about a longer history window to get a prediction interval'),
    ])
    expect(picked[0].label.length).toBeLessThanOrEqual(24)
    // The full question is still what gets asked.
    expect(picked[0].question.length).toBeGreaterThan(picked[0].label.length)
  })

  it('deduplicates the same question from two rules', () => {
    const picked = selectFollowUpQuestions([
      chip('By Region', 'Break down revenue by Region'),
      chip('Regionally', 'break down revenue by region'),
    ])
    expect(picked).toHaveLength(1)
  })

  it('deduplicates two chips that share a label', () => {
    // Two different questions presented under one identical chip give the
    // reader no way to tell them apart.
    const picked = selectFollowUpQuestions([
      chip('Top 5', 'Top 5 products by revenue'),
      chip('Top 5', 'Top 5 regions by revenue'),
    ])
    expect(picked).toHaveLength(1)
  })

  it('caps the list so it stays a suggestion, not a second nav surface', () => {
    const many = Array.from({ length: 9 }, (_, index) =>
      chip(`Label ${index}`, `Question ${index}?`),
    )
    expect(selectFollowUpQuestions(many)).toHaveLength(4)
  })

  it('drops an empty or whitespace-only question', () => {
    expect(selectFollowUpQuestions([recommendation('   ')])).toEqual([])
  })

  it('drops a chip whose action is only whitespace', () => {
    expect(selectFollowUpQuestions([chip('   ', 'Show the trend over time')])).toEqual([])
  })

  it('normalizes a database-derived label and question', () => {
    // `sanitizeLabel` replaces control characters, collapses whitespace
    // and caps the length. It does not strip markup, and does not need
    // to: React escapes on render, and a clicked chip goes back through
    // the normal /ask path, so the input guard, the governance request
    // policy and the SQL validator all apply to it. What this prevents
    // is a newline-spliced instruction or an unbounded label.
    const picked = selectFollowUpQuestions([
      chip(`By${NEWLINE}Region`, `Revenue   by${NEWLINE}${NEWLINE}region${BELL} ?`),
    ])
    expect(picked[0].label).toBe('By Region')
    expect(picked[0].question).toBe('Revenue by region ?')
  })

  it('caps an unbounded question', () => {
    const picked = selectFollowUpQuestions([recommendation('x'.repeat(500))])
    expect(picked[0].question.length).toBeLessThanOrEqual(200)
  })

  it('returns nothing for no recommendations', () => {
    expect(selectFollowUpQuestions([])).toEqual([])
  })

  it('returns nothing when the server produced no follow-ups', () => {
    // The empty-result case: `recommendation.followups` offers nothing to
    // drill into, so there is nothing to render.
    expect(
      selectFollowUpQuestions([
        recommendation('Review concentration risk', { kind: 'action', action: 'Review' }),
      ]),
    ).toEqual([])
  })
})

describe('FollowUpSuggestions', () => {
  it('renders nothing when there are no follow-up questions', () => {
    const { container } = render(
      <FollowUpSuggestions
        recommendations={[recommendation('Do a thing', { kind: 'action' })]}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing at all for an empty recommendation list', () => {
    const { container } = render(<FollowUpSuggestions recommendations={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders one chip per question under a labelled list', () => {
    render(
      <FollowUpSuggestions
        recommendations={[
          chip('Top 5', 'Top 5 products by revenue'),
          chip('Show trend', 'Show revenue over time'),
        ]}
      />,
    )
    const list = screen.getByRole('list', { name: 'Ask next' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
  })

  it('shows the short label as the visible chip text', () => {
    render(
      <FollowUpSuggestions
        recommendations={[chip('By Region', 'Break down Net Revenue by Sales Region')]}
        onAsk={vi.fn()}
      />,
    )
    expect(screen.getByText('By Region')).toBeInTheDocument()
    expect(screen.queryByText('Break down Net Revenue by Sales Region')).not.toBeInTheDocument()
  })

  it('names the chip to assistive tech with the whole question', () => {
    // A screen-reader user must never be asked to act on "By Region"
    // without knowing what it asks.
    render(
      <FollowUpSuggestions
        recommendations={[chip('By Region', 'Break down Net Revenue by Sales Region')]}
        onAsk={vi.fn()}
      />,
    )
    expect(
      screen.getByRole('button', { name: 'Break down Net Revenue by Sales Region' }),
    ).toBeInTheDocument()
  })

  it('asks the full question when a chip is clicked, not the short label', () => {
    const onAsk = vi.fn()
    render(
      <FollowUpSuggestions
        recommendations={[chip('By Region', 'Break down Net Revenue by Sales Region')]}
        onAsk={onAsk}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Break down Net Revenue by Sales Region' }))
    expect(onAsk).toHaveBeenCalledWith('Break down Net Revenue by Sales Region')
  })

  it('submits natural language, never SQL', () => {
    // The frontend builds no query. A click re-enters the ordinary /ask
    // path, where the input guard and the SQL validator apply.
    const onAsk = vi.fn()
    render(
      <FollowUpSuggestions
        recommendations={[chip('Top 5', 'Top 5 Product Category by Sales Amount')]}
        onAsk={onAsk}
      />,
    )
    fireEvent.click(screen.getAllByRole('button')[0])
    const asked: string = onAsk.mock.calls[0][0]
    for (const token of ['SELECT ', 'FROM ', 'WHERE ', 'GROUP BY', ';']) {
      expect(asked.toUpperCase()).not.toContain(token)
    }
  })

  it('passes the normalized text to onAsk, not the raw label', () => {
    const onAsk = vi.fn()
    render(
      <FollowUpSuggestions
        recommendations={[chip('By EMEA', `Revenue  for${NEWLINE}EMEA?`)]}
        onAsk={onAsk}
      />,
    )
    fireEvent.click(screen.getAllByRole('button')[0])
    expect(onAsk).toHaveBeenCalledWith('Revenue for EMEA?')
  })

  it('renders static chips when no handler is supplied', () => {
    // A reloaded past turn has no live ask handler.
    render(<FollowUpSuggestions recommendations={[chip('Top 5', 'Top 5 products')]} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('Top 5')).toBeInTheDocument()
  })

  it('disables the chips while another question is in flight', () => {
    // Preserves user control: a click cannot queue a second run behind
    // the first.
    render(
      <FollowUpSuggestions
        recommendations={[chip('Top 5', 'Top 5 products')]}
        onAsk={vi.fn()}
        disabled
      />,
    )
    expect(screen.getByRole('button', { name: 'Top 5 products' })).toBeDisabled()
  })

  it('does not ask anything when a disabled chip is clicked', () => {
    const onAsk = vi.fn()
    render(
      <FollowUpSuggestions
        recommendations={[chip('Top 5', 'Top 5 products')]}
        onAsk={onAsk}
        disabled
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Top 5 products' }))
    expect(onAsk).not.toHaveBeenCalled()
  })

  it('states that the suggestions are AI estimates', () => {
    render(<FollowUpSuggestions recommendations={[chip('Top 5', 'Top 5 products')]} />)
    expect(screen.getByText(/AI estimate/i)).toBeInTheDocument()
  })
})
