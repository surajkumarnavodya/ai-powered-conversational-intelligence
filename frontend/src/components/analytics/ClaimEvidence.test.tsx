import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ClaimEvidence, EvidenceList } from './ClaimEvidence'

describe('ClaimEvidence -- a claim and its evidence status', () => {
  it('shows the claim with its status beneath it', () => {
    render(<ClaimEvidence claim="Revenue increased 18%" status="computed" />)
    expect(screen.getByText('Revenue increased 18%')).toBeInTheDocument()
    expect(screen.getByText('Computed from data')).toBeInTheDocument()
  })

  it('marks an estimate as one', () => {
    render(<ClaimEvidence claim="Expected next-quarter revenue: 2.8 Cr" status="estimate" />)
    expect(screen.getByText('Expected next-quarter revenue: 2.8 Cr')).toBeInTheDocument()
    expect(screen.getByText('AI estimate')).toBeInTheDocument()
  })

  it('states that evidence is missing rather than showing a bare claim', () => {
    render(<ClaimEvidence claim="Churn will fall next month" status="unavailable" />)
    expect(screen.getByText('Not available')).toBeInTheDocument()
  })

  it('cannot render a claim with no status at all', () => {
    // `status` is a required prop, so there is no way to render an
    // unlabelled claim -- the property is enforced by the type, not by
    // remembering to pass something. This asserts the rendered
    // consequence: every claim row carries a status badge.
    const { container } = render(<ClaimEvidence claim="Some claim" status="computed" />)
    expect(container.querySelector('[data-evidence-status]')).not.toBeNull()
  })
})

describe('ClaimEvidence -- expand and collapse', () => {
  it('hides the evidence until asked, keeping the claim and status visible', () => {
    render(
      <ClaimEvidence
        claim="Revenue increased 18%"
        status="computed"
        summary="2 figures in this summary were checked against the 42 returned rows."
        details={<p>Growth computed from the returned period totals.</p>}
      />,
    )
    // Collapsed: claim and status visible, evidence not in the document.
    expect(screen.getByText('Revenue increased 18%')).toBeInTheDocument()
    expect(screen.getByText('Computed from data')).toBeInTheDocument()
    expect(screen.queryByText(/Growth computed from the returned period totals/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Evidence' }))
    expect(screen.getByText(/Growth computed from the returned period totals/)).toBeInTheDocument()
    expect(screen.getByText(/2 figures in this summary were checked/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Hide evidence' }))
    expect(screen.queryByText(/Growth computed from the returned period totals/)).not.toBeInTheDocument()
  })

  it('can start open, for a surface whose purpose is the evidence', () => {
    render(
      <ClaimEvidence
        claim="Bikes account for half of revenue"
        status="estimate"
        defaultOpen
        details={<p>Bikes share is 50.0% of total revenue</p>}
      />,
    )
    expect(screen.getByText('Bikes share is 50.0% of total revenue')).toBeInTheDocument()
  })

  it('offers no disclosure when there is genuinely no evidence to show', () => {
    // Rather than a control that opens onto an empty box.
    render(<ClaimEvidence claim="A claim" status="unavailable" />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('explains the status inside the disclosure', () => {
    render(
      <ClaimEvidence claim="A claim" status="approved" details={<p>detail</p>} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Evidence' }))
    expect(screen.getByText(/metric definition a reviewer approved/)).toBeInTheDocument()
  })

  it('wires the toggle to its panel for assistive technology', () => {
    render(<ClaimEvidence claim="A claim" status="computed" details={<p>detail</p>} />)
    const toggle = screen.getByRole('button', { name: 'Evidence' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    const controls = toggle.getAttribute('aria-controls')
    expect(controls).toBeTruthy()

    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'Hide evidence' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(document.getElementById(controls as string)).not.toBeNull()
  })
})

describe('EvidenceList -- multi-source evidence', () => {
  it('keeps each piece of evidence at its own level rather than merging them', () => {
    // A single estimate can rest on both a computed figure and an
    // approved definition. Folding those into the claim's own status
    // would lose exactly the distinction that matters.
    render(
      <EvidenceList
        items={[
          { level: 'database_fact', text: 'Bikes share is 50.0% of total revenue' },
          { level: 'confirmed_business_truth', text: 'Revenue = SUM(SalesAmount)' },
        ]}
      />,
    )
    const list = screen.getByRole('list', { name: 'Evidence' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Computed from data')
    expect(items[0]).toHaveTextContent('Bikes share is 50.0% of total revenue')
    expect(items[1]).toHaveTextContent('Approved definition')
    expect(items[1]).toHaveTextContent('Revenue = SUM(SalesAmount)')
  })

  it('keeps each item data-truth-level, which panels assert on', () => {
    const { container } = render(
      <EvidenceList
        items={[
          { level: 'database_fact', text: 'a' },
          { level: 'confirmed_business_truth', text: 'b' },
        ]}
      />,
    )
    expect(container.querySelector('[data-truth-level="database_fact"]')).not.toBeNull()
    expect(container.querySelector('[data-truth-level="confirmed_business_truth"]')).not.toBeNull()
  })

  it('renders nothing for an empty list', () => {
    const { container } = render(<EvidenceList items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('sanitizes evidence text that came from the database', () => {
    const noisy = `Region${String.fromCharCode(10)}North${String.fromCharCode(9)}West`
    render(<EvidenceList items={[{ level: 'database_fact', text: noisy }]} />)
    // Control characters are collapsed, so a value cannot break the row.
    expect(screen.getByText('Region North West')).toBeInTheDocument()
  })
})
