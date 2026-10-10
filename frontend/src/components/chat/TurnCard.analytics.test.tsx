import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// The SQL editor is CodeMirror, which throws inside jsdom while measuring
// (`textRange(...).getClientRects is not a function`) and makes mounting a
// turn slow enough to race this file's `findByRole` on the lazily-loaded
// analysis section. That was already an intermittent failure before this
// file grew; stubbing the editor removes both the noise and the race. This
// file is about the analysis section, not the editor -- TurnCard.test.tsx
// is what covers the editor's own gating.
vi.mock('@/components/sql/SqlEditor', () => ({
  SqlEditor: () => <div data-testid="sql-editor-stub" />,
}))
import { describe, expect, it, vi } from 'vitest'
import { newHistoryEntry, withConfirmedResult } from '@/lib/history'
import { askState, rankingResult } from '@/test/analyticsFixtures'
import { TurnCard } from './TurnCard'

/** Budget for a lazily-imported section to mount. Generous on purpose:
 * this waits on a genuine dynamic import, which competes with every other
 * worker when the full suite runs in parallel. */
const LAZY_IMPORT_TIMEOUT_MS = 10_000

// TurnCard renders a react-router <Link> (the lineage link inside
// SqlIntelligencePanel), so the component needs router context -- which it
// has in the real app, where it is rendered under a route. MemoryRouter is
// the convention the rest of this suite already uses (App.routes.test.tsx,
// AppShell.test.tsx).
function renderTurn(entry: ReturnType<typeof newHistoryEntry>) {
  const queryClient = new QueryClient()
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <TurnCard entry={entry} isMultiDb={false} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

const SQL = 'SELECT category, SUM(revenue) FROM sales GROUP BY category'

describe('TurnCard -- analysis section (Prompt 30)', () => {
  it('shows the analysis only after the user confirms and runs the SQL, like the insight it sits beside', () => {
    const entry = newHistoryEntry(
      'Revenue by category',
      askState({ sql: SQL, analytical_result: rankingResult, analytical_intent: null }),
      100,
    )
    renderTurn(entry)

    expect(screen.queryByRole('region', { name: 'Analysis' })).not.toBeInTheDocument()
  })

  it('renders the analysis, with its freshness, once a result is confirmed', async () => {
    const base = newHistoryEntry(
      'Revenue by category',
      askState({ sql: SQL, analytical_result: rankingResult }),
      100,
    )
    const confirmed = withConfirmedResult(
      base,
      ['category', 'revenue'],
      [
        ['Bikes', 500],
        ['Clothing', 300],
        ['Accessories', 200],
      ],
      SQL,
      { category: 'text', revenue: 'numeric' },
      null,
      false,
      25,
      'hit',
    )
    renderTurn(confirmed)

    // The analysis section is lazy-loaded (see TurnCard.tsx), so it resolves after mount.
    // `AnalyticsSummary` is behind a real `lazy()` dynamic import (so
    // Chart.js stays out of the main bundle), and resolving that import
    // can exceed testing-library's 1s default when the whole suite runs in
    // parallel. This waits on the real boundary rather than masking a
    // failure: the assertions below are unchanged, and the file still
    // passes in well under a second on its own.
    const analysis = await screen.findByRole(
      'region',
      { name: 'Analysis' },
      { timeout: LAZY_IMPORT_TIMEOUT_MS },
    )
    expect(analysis).toHaveTextContent('Served from a recent cached result')
    expect(
      await screen.findByRole('list', { name: 'Key figures' }, { timeout: LAZY_IMPORT_TIMEOUT_MS }),
    ).toBeInTheDocument()
  })

  it('does not show the analysis when the user has edited the SQL away from what produced it', () => {
    const base = newHistoryEntry(
      'Revenue by category',
      askState({ sql: SQL, analytical_result: rankingResult }),
      100,
    )
    const confirmed = {
      ...withConfirmedResult(
        base,
        ['category', 'revenue'],
        [['Bikes', 500]],
        SQL,
        { category: 'text', revenue: 'numeric' },
        null,
        false,
        25,
        null,
      ),
      editableSql: 'SELECT 1',
    }
    renderTurn(confirmed)

    expect(screen.queryByRole('region', { name: 'Analysis' })).not.toBeInTheDocument()
  })

  it('shows a plain-language field-permission notice for a failed answer blocked by a restricted column', () => {
    const entry = newHistoryEntry(
      'Show customer SSNs',
      askState({
        status: 'failed',
        sql: 'SELECT ssn FROM customers',
        failure_explanation: 'Agent could not produce a working query.',
        restricted_field_notice:
          'One or more fields this question needs are access-restricted for your role, so the answer could not be produced. Ask an administrator if you need access.',
      }),
      100,
    )
    renderTurn(entry)

    expect(screen.getByText(/access-restricted for your role/)).toBeInTheDocument()
  })

  it('does not show a field-permission notice for an ordinary failure', () => {
    const entry = newHistoryEntry(
      'Something broken',
      askState({
        status: 'failed',
        sql: 'SELECT broken',
        failure_explanation: 'Agent could not produce a working query.',
        restricted_field_notice: null,
      }),
      100,
    )
    renderTurn(entry)

    expect(screen.queryByText(/access-restricted/)).not.toBeInTheDocument()
  })
})
