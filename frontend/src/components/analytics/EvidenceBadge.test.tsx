import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { TruthLevel } from '@/lib/types'
import {
  EvidenceBadge,
  evidenceStatusExplanation,
  evidenceStatusForTruthLevel,
  evidenceStatusLabel,
  type EvidenceStatus,
} from './EvidenceBadge'
import { TruthLevelBadge } from './TruthLevelBadge'

const ALL_STATUSES: EvidenceStatus[] = [
  'computed',
  'approved',
  'estimate',
  'source_backed',
  'unavailable',
]

describe('evidence labels', () => {
  it('uses the agreed wording for each status', () => {
    expect(evidenceStatusLabel('computed')).toBe('Computed from data')
    expect(evidenceStatusLabel('approved')).toBe('Approved definition')
    expect(evidenceStatusLabel('estimate')).toBe('AI estimate')
    expect(evidenceStatusLabel('source_backed')).toBe('Source-backed')
    expect(evidenceStatusLabel('unavailable')).toBe('Not available')
  })

  it('gives every status a distinct label and a non-empty explanation', () => {
    const labels = ALL_STATUSES.map(evidenceStatusLabel)
    expect(new Set(labels).size).toBe(ALL_STATUSES.length)
    for (const status of ALL_STATUSES) {
      expect(evidenceStatusExplanation(status).length).toBeGreaterThan(0)
    }
  })

  it('maps each server truth level onto its own status', () => {
    expect(evidenceStatusForTruthLevel('database_fact')).toBe('computed')
    expect(evidenceStatusForTruthLevel('confirmed_business_truth')).toBe('approved')
    expect(evidenceStatusForTruthLevel('ai_inference')).toBe('estimate')
  })

  it('keeps an approved definition distinguishable from an AI interpretation', () => {
    // The one confusion this vocabulary exists to prevent: a reviewed,
    // published metric definition must never read like a model's guess.
    expect(evidenceStatusForTruthLevel('confirmed_business_truth')).not.toBe(
      evidenceStatusForTruthLevel('ai_inference'),
    )
    expect(evidenceStatusLabel('approved')).not.toContain('AI')
    expect(evidenceStatusLabel('estimate')).toContain('AI')
  })
})

describe('EvidenceBadge', () => {
  it('renders the label as text, not only as a colour', () => {
    render(<EvidenceBadge status="computed" />)
    expect(screen.getByText('Computed from data')).toBeInTheDocument()
  })

  it('marks an estimate visibly rather than leaving it unlabelled', () => {
    render(<EvidenceBadge status="estimate" />)
    expect(screen.getByText('AI estimate')).toBeInTheDocument()
  })

  it('states missing evidence instead of rendering nothing', () => {
    // An unlabelled claim reads as an endorsed one, so this must be a
    // visible state.
    const { container } = render(<EvidenceBadge status="unavailable" />)
    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(container).not.toBeEmptyDOMElement()
  })

  it('exposes the status for styling and assertion', () => {
    const { container } = render(<EvidenceBadge status="source_backed" />)
    expect(container.querySelector('[data-evidence-status="source_backed"]')).not.toBeNull()
  })

  it('carries data-truth-level only when the status came from one', () => {
    // `source_backed` and `unavailable` have no server truth level, so
    // claiming one would be inventing provenance.
    const withLevel = render(<EvidenceBadge status="computed" truthLevel="database_fact" />)
    expect(
      withLevel.container.querySelector('[data-truth-level="database_fact"]'),
    ).not.toBeNull()

    const without = render(<EvidenceBadge status="unavailable" />)
    expect(without.container.querySelector('[data-truth-level]')).toBeNull()
  })

  it('keeps its icon out of the accessibility tree', () => {
    const { container } = render(<EvidenceBadge status="approved" />)
    expect(container.querySelectorAll('svg[aria-hidden="true"]')).toHaveLength(1)
  })

  it('never exposes model reasoning or a credential in any status', () => {
    for (const status of ALL_STATUSES) {
      const text = `${evidenceStatusLabel(status)} ${evidenceStatusExplanation(status)}`
      expect(text).not.toMatch(/system prompt|chain of thought|reasoning:/i)
      expect(text).not.toMatch(/password|Server=|connection string/i)
    }
  })
})

describe('TruthLevelBadge -- the pre-existing call sites', () => {
  const LEVELS: TruthLevel[] = ['database_fact', 'ai_inference', 'confirmed_business_truth']

  it('renders the same label the shared vocabulary defines', () => {
    // One owner of the label text: this component delegates rather than
    // keeping a second copy, so "Computed from data" cannot drift between
    // the analytics panels and the newer evidence surfaces.
    for (const level of LEVELS) {
      const { unmount } = render(<TruthLevelBadge level={level} />)
      expect(
        screen.getByText(evidenceStatusLabel(evidenceStatusForTruthLevel(level))),
      ).toBeInTheDocument()
      unmount()
    }
  })

  it('still stamps data-truth-level for every level', () => {
    // Asserted by AnalyticsSummary.test.tsx and DecisionCenter.test.tsx.
    for (const level of LEVELS) {
      const { container, unmount } = render(<TruthLevelBadge level={level} />)
      expect(container.querySelector(`[data-truth-level="${level}"]`)).not.toBeNull()
      unmount()
    }
  })
})
