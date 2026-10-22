import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { askState } from '@/test/analyticsFixtures'
import { SourcesUsedChips, SourcesUsedPanel } from './SourcesUsedPanel'

/** The no-retrieval route: the router answered "general_knowledge" or
 * "conversation", no source ran, and the whole answer is in
 * `synthesized_answer`. Gating the panel on `sources_used` alone rendered
 * nothing at all for these turns -- see agent/orchestrator/nodes.py's
 * `_no_retrieval_answer`. */
const NO_RETRIEVAL = askState({
  sources_used: [],
  synthesized_answer: "I couldn't match that question to any of the connected data sources.",
  sql: null,
  result_rows: [],
  row_count: 0,
})

describe('SourcesUsedPanel', () => {
  it('renders the answer for a turn where no source ran', () => {
    render(<SourcesUsedPanel state={NO_RETRIEVAL} entryId="e1" />)

    expect(
      screen.getByText(/couldn't match that question to any of the connected data sources/),
    ).toBeInTheDocument()
  })

  it('still renders nothing when there is neither a source nor an answer', () => {
    const { container } = render(
      <SourcesUsedPanel
        state={askState({ sources_used: [], synthesized_answer: null })}
        entryId="e1"
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('shows no attribution chips for a turn where no source ran', () => {
    // The chip row and the answer body are two different owners: there is
    // genuinely nothing to attribute here, so "Sources:" must not appear.
    const { container } = render(<SourcesUsedChips state={NO_RETRIEVAL} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('still renders the combined answer when sources did run', () => {
    render(
      <SourcesUsedPanel
        state={askState({ sources_used: ['sql', 'policy'], synthesized_answer: 'Combined answer.' })}
        entryId="e1"
      />,
    )

    expect(screen.getByText('Combined answer.')).toBeInTheDocument()
  })
})
