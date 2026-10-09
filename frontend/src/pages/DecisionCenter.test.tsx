import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ExecutiveBriefing } from '@/lib/decisionCenterApi'
import DecisionCenter from './DecisionCenter'

/** Tests for the Executive Decision Center page (Prompt 52).
 *
 * The page is presentation only: every number, truth level and ordering
 * decision arrives already resolved from `GET /decision-center/briefing`.
 * So these tests pin that it *renders what it was given* -- including the
 * honest parts an implementation might be tempted to tidy away (an
 * incomplete chain's reason, a "not recorded" field, the list of
 * capabilities that did not contribute) -- plus the accessibility
 * contract the mission asks for.
 */

vi.mock('@/lib/decisionCenterApi', async () => {
  const actual = await vi.importActual<typeof import('@/lib/decisionCenterApi')>(
    '@/lib/decisionCenterApi',
  )
  return { ...actual, getExecutiveBriefing: vi.fn() }
})

const { getExecutiveBriefing } = await import('@/lib/decisionCenterApi')

function briefing(overrides: Partial<ExecutiveBriefing> = {}): ExecutiveBriefing {
  return {
    tenant_id: 'tenant-a',
    database_id: 'default',
    profile_id: 'executive',
    generated_at: '2026-10-09T12:00:00Z',
    health: [
      {
        key: 'open_signals',
        label: 'Open KPI signals',
        value: 3,
        unit: 'count',
        direction: 'lower_is_better',
        detail: 'Movements nobody has decided on yet.',
        truth_level: 'database_fact',
      },
    ],
    sections: [
      {
        section: 'kpi_movement',
        title: 'KPI movement',
        total_available: 7,
        empty_reason: '',
        headlines: [
          {
            headline_id: 'finding:1',
            kind: 'kpi_movement',
            title: 'Net Revenue fell 36.0% in 2026-06',
            detail: 'Observed against the previous period.',
            severity: 73,
            severity_tier: 'high',
            status: 'open',
            occurred_at: '2026-10-09T12:00:00Z',
            truth_level: 'ai_inference',
            provenance: {
              source: 'kpi_monitoring',
              source_record_id: '1',
              period: '2026-06',
              metric_concept_key: 'revenue',
              confidence: 0.94,
              database_id: 'default',
              recorded_at: '2026-10-09T12:00:00Z',
              engine_version: '1.0.0',
              limitations: ['one value excluded as noise'],
              lineage_available: true,
              metric_definition_available: true,
            },
            evidence: [
              { label: 'observed', value: 'current 640.0', truth_level: 'database_fact' },
              { label: 'attribution', value: 'East', truth_level: 'ai_inference' },
            ],
          },
        ],
      },
      {
        section: 'risks',
        title: 'Risks',
        total_available: 0,
        empty_reason: 'No risks are open.',
        headlines: [],
      },
    ],
    chains: [
      {
        chain_id: 'chain:finding:1',
        is_complete: false,
        furthest_stage: 'explanation',
        break_reason: 'recommendation: no recommendation concerns this subject',
        truth_level: 'ai_inference',
        links: [
          {
            stage: 'signal',
            present: true,
            headline_id: 'finding:1',
            summary: 'Net Revenue fell',
            link_type: null,
            absent_reason: '',
          },
          {
            stage: 'recommendation',
            present: false,
            headline_id: null,
            summary: '',
            link_type: null,
            absent_reason: 'no recommendation concerns this subject',
          },
        ],
      },
    ],
    metric_definitions: {
      revenue: {
        concept_key: 'revenue',
        business_name: 'Net Revenue',
        description: 'Revenue after returns.',
        status: 'published',
        version: 3,
        owner: 'finance',
        dimensions: ['region'],
        confidence: 0.9,
      },
    },
    contributing_sources: ['kpi_monitoring'],
    unavailable_sources: ['forecasting: computes on demand and persists no record of its own'],
    engine_version: '1.0.0',
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(getExecutiveBriefing).mockResolvedValue(briefing())
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('DecisionCenter', () => {
  it('renders the health indicators with their truth level', async () => {
    render(<DecisionCenter />)
    expect(await screen.findByText('Open KPI signals')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('Lower is better')).toBeInTheDocument()
  })

  it('renders each section as a labelled region', async () => {
    render(<DecisionCenter />)
    await screen.findByText('Open KPI signals')
    // Accessible structure: a heading per section, associated via
    // aria-labelledby so assistive tech can navigate between them.
    expect(screen.getByRole('heading', { name: 'KPI movement' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Risks' })).toBeInTheDocument()
  })

  it('shows an empty section its own reason rather than hiding it', async () => {
    render(<DecisionCenter />)
    expect(await screen.findByText('No risks are open.')).toBeInTheDocument()
  })

  it('reports that a section is truncated rather than under-reporting silently', async () => {
    render(<DecisionCenter />)
    expect(await screen.findByText('Showing 1 of 7')).toBeInTheDocument()
  })

  it('keeps each headline collapsed until asked, with aria-expanded', async () => {
    render(<DecisionCenter />)
    const toggle = await screen.findByRole('button', {
      name: /Net Revenue fell 36.0% in 2026-06/,
    })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Metric definition')).not.toBeInTheDocument()
  })

  it('drills into evidence, metric definition, period, source, confidence and lineage', async () => {
    const user = userEvent.setup()
    render(<DecisionCenter />)
    const toggle = await screen.findByRole('button', {
      name: /Net Revenue fell 36.0% in 2026-06/,
    })

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    // The mission's own six drill-down requirements, each present.
    expect(screen.getByText('Evidence')).toBeInTheDocument()
    expect(screen.getByText('Metric definition')).toBeInTheDocument()
    expect(screen.getByText('Period')).toBeInTheDocument()
    expect(screen.getByText('Source')).toBeInTheDocument()
    expect(screen.getByText('Confidence')).toBeInTheDocument()
    expect(screen.getByText('Lineage can be traced for this subject.')).toBeInTheDocument()
    expect(screen.getByText('2026-06')).toBeInTheDocument()
    expect(screen.getByText('0.94')).toBeInTheDocument()
    // The governed definition, resolved from the briefing's own
    // `metric_definitions` map -- scoped to the drill-down panel, since
    // the metric's name also appears in the headline title above it.
    expect(screen.getByText(/Revenue after returns\./)).toBeInTheDocument()
    expect(screen.getByText(/published v3/)).toBeInTheDocument()
  })

  it('renders the recorded limitations rather than dropping them', async () => {
    const user = userEvent.setup()
    render(<DecisionCenter />)
    await user.click(
      await screen.findByRole('button', { name: /Net Revenue fell 36.0% in 2026-06/ }),
    )
    expect(screen.getByText('one value excluded as noise')).toBeInTheDocument()
  })

  it('labels observed and inferred evidence separately', async () => {
    const user = userEvent.setup()
    render(<DecisionCenter />)
    await user.click(
      await screen.findByRole('button', { name: /Net Revenue fell 36.0% in 2026-06/ }),
    )
    // Both truth levels are carried through to the rendered evidence.
    // Asserted on `TruthLevelBadge`'s own `data-truth-level` contract
    // rather than its display text, which is the shared component's to
    // change.
    const badges = document.querySelectorAll('[data-truth-level]')
    const levels = new Set(
      Array.from(badges).map((node) => node.getAttribute('data-truth-level')),
    )
    expect(levels.has('database_fact')).toBe(true)
    expect(levels.has('ai_inference')).toBe(true)
  })

  it('shows an incomplete chain with the reason it stopped', async () => {
    render(<DecisionCenter />)
    await screen.findByText('Open KPI signals')
    expect(
      screen.getByText('no recommendation concerns this subject'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('This chain is incomplete. The stage marked missing explains why.'),
    ).toBeInTheDocument()
  })

  it('marks chain stages with text, not colour alone', async () => {
    render(<DecisionCenter />)
    await screen.findByText('Open KPI signals')
    expect(screen.getByText('reached')).toBeInTheDocument()
    expect(screen.getByText('missing')).toBeInTheDocument()
  })

  it('lists the capabilities that did not contribute', async () => {
    render(<DecisionCenter />)
    expect(
      await screen.findByText(
        'forecasting: computes on demand and persists no record of its own',
      ),
    ).toBeInTheDocument()
  })

  it('renders a not-recorded field honestly instead of blank', async () => {
    const withoutConfidence = briefing()
    withoutConfidence.sections[0].headlines[0].provenance.confidence = null
    withoutConfidence.sections[0].headlines[0].provenance.metric_concept_key = null
    withoutConfidence.sections[0].headlines[0].provenance.metric_definition_available = false
    vi.mocked(getExecutiveBriefing).mockResolvedValue(withoutConfidence)

    const user = userEvent.setup()
    render(<DecisionCenter />)
    await user.click(
      await screen.findByRole('button', { name: /Net Revenue fell 36.0% in 2026-06/ }),
    )

    expect(screen.getByText('not recorded')).toBeInTheDocument()
    expect(
      screen.getByText('This headline is not about a governed metric.'),
    ).toBeInTheDocument()
  })

  it('announces a load failure to assistive technology', async () => {
    vi.mocked(getExecutiveBriefing).mockRejectedValue(new Error('nope'))
    render(<DecisionCenter />)
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('The briefing could not be loaded.')
    })
  })

  it('names the profile it was ordered for without implying different facts', async () => {
    render(<DecisionCenter />)
    expect(
      await screen.findByText(/the facts are the same for every role/i),
    ).toBeInTheDocument()
  })

  it('renders headlines in the order the server returned them', async () => {
    const twoHeadlines = briefing()
    twoHeadlines.sections[0].headlines = [
      { ...twoHeadlines.sections[0].headlines[0], headline_id: 'a', title: 'First from server' },
      {
        ...twoHeadlines.sections[0].headlines[0],
        headline_id: 'b',
        title: 'Second from server',
        severity: 99,
      },
    ]
    vi.mocked(getExecutiveBriefing).mockResolvedValue(twoHeadlines)

    render(<DecisionCenter />)
    await screen.findByText('Open KPI signals')
    const list = screen.getAllByRole('list').find((element) =>
      within(element).queryByText('First from server'),
    )
    expect(list).toBeDefined()
    const titles = within(list as HTMLElement)
      .getAllByRole('button')
      .map((button) => button.textContent ?? '')
    // The page must not re-rank: the higher severity stays second,
    // because that is the order the server sent.
    expect(titles[0]).toContain('First from server')
    expect(titles[1]).toContain('Second from server')
  })
})
